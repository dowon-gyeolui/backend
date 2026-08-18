"""네이티브 앱 로그인용 1회용 코드 — 딥링크로 JWT 를 직접 흘리지 않기 위한 것.

커스텀 스킴(``com.melobe.app://``)은 OS 가 소유권을 검증하지 않는다. 같은 스킴을
등록한 악성 앱이 카카오 로그인 완료 리다이렉트를 가로챌 수 있고, 거기에 7일짜리
JWT 가 실려 있으면 그대로 계정 탈취다. 그래서 딥링크에는 2분짜리 1회용 코드만 싣고,
실제 토큰은 앱이 HTTPS 로 교환해 간다.

코드만 가로채도 교환할 수 없게 PKCE(RFC 7636, S256)로 묶는다. 앱이 로그인을 시작할 때
난수 ``code_verifier`` 를 만들어 그 해시(``code_challenge``)만 서버로 보내고, 교환 시점에
원본을 제시한다. verifier 는 로그인을 시작한 앱 안에만 있으므로, 딥링크를 가로챈 앱은
코드를 손에 넣어도 토큰을 받지 못한다.

저장은 ``app.services.cache`` 를 쓴다. ``REDIS_URL`` 이 없으면 프로세스 메모리로 떨어지므로
인스턴스가 2개 이상인 운영 환경에서는 REDIS_URL 이 반드시 설정되어야 한다
(코드를 발급한 인스턴스와 교환을 받는 인스턴스가 달라지면 로그인이 실패한다).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets

from app.services.cache import cache_pop, cache_set

_KEY_PREFIX = "applogin:"
_TTL_S = 120

# S256 챌린지는 32바이트 해시의 base64url(패딩 없음) — 항상 43자다.
CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _challenge_of(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def issue_login_code(user_id: int, is_new: bool, code_challenge: str) -> str:
    """로그인 완료 사실을 1회용 코드로 바꿔 돌려준다. 코드 자체에는 정보가 없다."""
    code = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": user_id, "is_new": is_new, "challenge": code_challenge})
    await cache_set(_KEY_PREFIX + code, payload, _TTL_S)
    return code


async def redeem_login_code(code: str, code_verifier: str) -> tuple[int, bool] | None:
    """코드를 소진하고 (user_id, is_new) 를 돌려준다. 만료·재사용·verifier 불일치면 None."""
    raw = await cache_pop(_KEY_PREFIX + code)
    if raw is None:
        return None

    data = json.loads(raw)
    if not secrets.compare_digest(data["challenge"], _challenge_of(code_verifier)):
        return None
    return data["user_id"], data["is_new"]
