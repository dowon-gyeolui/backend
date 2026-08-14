"""시크릿이 응답·로그로 평문으로 나가는 것을 막는 마스킹 헬퍼.

`backend/CLAUDE.md` 의 약속: ".env 의 시크릿이 응답·로그에 평문으로 나가지 않도록 마스킹".

외부 SDK(cloudinary·openai·boto3 등)의 예외 메시지에는 접속 URL 이나 API 키가 그대로
섞여 있을 수 있다. 예외를 문자열로 만들어 밖(HTTP 응답·stdout)으로 내보내는 곳은
반드시 `redact_secrets()` 를 거친다. 회귀는 `tests/test_health_and_secrets.py` 가 잡는다.
"""

from __future__ import annotations

import os
import re

from app.config import settings

MASK = "***"

# 이 길이 미만의 값은 마스킹 대상에서 뺀다. 짧은 값(빈 문자열·한두 글자)은
# 무관한 문자열까지 갉아먹어 메시지를 못 읽게 만든다.
_MIN_SECRET_LEN = 8

# Settings 에 없고 os.environ 으로만 읽는 시크릿들.
_SECRET_ENV_KEYS = (
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "CLOUDINARY_URL",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
)

# `scheme://user:password@host` 형태의 비밀번호. 설정값과 글자가 달라도(오타·변형)
# 잡히도록 알려진 값 치환과 별개로 한 번 더 훑는다.
_URL_CREDENTIALS_RE = re.compile(r"([A-Za-z][\w+.-]*://[^\s:/@]+):[^\s/@]+@")


def redact_url_credentials(url: str) -> str:
    """`scheme://user:password@host` 의 비밀번호만 가린다. 자격증명이 없으면 원문 그대로."""
    try:
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        userinfo, hostpart = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            return f"{scheme}://{user}:{MASK}@{hostpart}"
        return url
    except ValueError:
        # 형태를 알 수 없는 입력. 원문을 흘리느니 통째로 가린다.
        return MASK


def _secret_values() -> list[str]:
    """지금 설정에 실제로 들어 있는 시크릿 값들. 호출 시점에 읽는다."""
    candidates = [
        settings.database_url,
        settings.secret_key,
        settings.kakao_client_id,
        settings.kakao_client_secret,
        settings.kakao_admin_key,
        settings.toss_secret_key,
        settings.redis_url,
        settings.sentry_dsn,
        settings.firebase_service_account_json,
        *(os.environ.get(key, "") for key in _SECRET_ENV_KEYS),
    ]
    return [v for v in candidates if len(v) >= _MIN_SECRET_LEN]


def redact_secrets(text: str) -> str:
    """알려진 시크릿 값과 URL 자격증명을 가린다.

    예외 메시지를 HTTP 응답이나 로그에 싣기 전에 반드시 통과시킨다.
    긴 값부터 치환해야 CLOUDINARY_URL 처럼 다른 시크릿을 품은 값이 먼저 지워진다.
    """
    out = text
    for value in sorted(_secret_values(), key=len, reverse=True):
        out = out.replace(value, MASK)
    return _URL_CREDENTIALS_RE.sub(rf"\1:{MASK}@", out)
