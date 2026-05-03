"""Kakao OAuth 2.0 helpers.

Two HTTP calls are needed against Kakao's API:

1. ``POST https://kauth.kakao.com/oauth/token`` — exchange the ``code`` Kakao
   gives our redirect URI for an ``access_token``.
2. ``GET  https://kapi.kakao.com/v2/user/me`` — fetch the user's profile so we
   can grab a stable ``id`` (their kakao_id) and any nickname/photo.

Errors from Kakao are surfaced as ``HTTPException(400)`` so the caller's
redirect handler can display something reasonable instead of a generic 500.
"""
import logging
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"
KAKAO_UNLINK_URL = "https://kapi.kakao.com/v1/user/unlink"

# 카카오에 요청할 동의 항목. 콘솔(앱 설정 → 카카오 로그인 → 동의 항목)
# 에서 각 scope 의 "필수"/"선택" 여부를 설정해야 사용자에게 picker 가
# 노출된다. 모두 "필수" 로 두면 picker 없이 단일 동의 버튼만 뜸.
#
# scope 지정으로 카카오에 "이 항목들에 대해 (다시) 동의 받아라" 신호를
# 명시적으로 주는 효과 — unlink 후 silent grant 가 일어나는 것을 막는
# 부수 효과도 있음.
KAKAO_OAUTH_SCOPES = [
    "profile_nickname",   # 닉네임 (보통 필수)
    "profile_image",      # 프로필 이미지 (보통 필수)
    "account_email",      # 이메일 (선택 추천 — 알림용)
]


def kakao_authorize_url() -> str:
    """The Kakao consent page URL we redirect the user to from /auth/kakao."""
    params = {
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
        "scope": ",".join(KAKAO_OAUTH_SCOPES),
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://kauth.kakao.com/oauth/authorize?{qs}"


async def exchange_code_for_token(code: str) -> str:
    """Exchange an authorization code for a Kakao access token."""
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "code": code,
    }
    if settings.kakao_client_secret:
        data["client_secret"] = settings.kakao_client_secret

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(KAKAO_TOKEN_URL, data=data)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Kakao token exchange failed: {resp.text}",
        )

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=400, detail="Kakao response missing access_token"
        )
    return access_token


async def fetch_kakao_profile(access_token: str) -> dict[str, Any]:
    """Fetch the authenticated user's Kakao profile."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(KAKAO_USER_INFO_URL, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Kakao profile fetch failed: {resp.text}",
        )
    return resp.json()


async def unlink_kakao_user(kakao_id: str) -> None:
    """탈퇴 시 Kakao 측 동의 연결도 끊는다.

    Kakao 가 사용자 ↔ 우리 앱의 "동의 완료" 상태를 자체 보관하기 때문에,
    우리 DB 만 지우고 unlink 호출을 안 하면 같은 kakao_id 로 재가입할 때
    동의 화면이 뜨지 않고 silent login 으로 그냥 들어와버린다 — 사용자
    UX 도 어색하고 PIPA(개인정보보호법) 관점에서도 "더 이상 보유하지
    않는다"는 신호를 Kakao 에 보내지 않는 셈.

    어드민 키(KAKAO_ADMIN_KEY) 가 설정되어 있으면 access_token 없이도
    target_id_type=user_id 방식으로 unlink 가능. 키가 없으면 명확하게
    경고 로그를 남긴다 — silent skip 으로 인한 디버깅 어려움 방지.

    Kakao 가 4xx/5xx 를 돌려줘도 우리 쪽 탈퇴는 진행한다 — 사용자가
    이미 다른 데서 unlink 했거나 Kakao 일시 장애 일 수 있고, 그렇다고
    해서 우리 DB 의 탈퇴 자체가 막혀선 안 됨.
    """
    if not settings.kakao_admin_key:
        logger.warning(
            "KAKAO_ADMIN_KEY 가 설정되지 않아 unlink 호출을 스킵합니다. "
            "재가입 시 동의 화면이 뜨지 않을 수 있습니다. "
            "Render Dashboard 에서 KAKAO_ADMIN_KEY 환경변수를 설정해주세요. "
            "(kakao_id=%s)",
            kakao_id,
        )
        return

    headers = {
        "Authorization": f"KakaoAK {settings.kakao_admin_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "target_id_type": "user_id",
        "target_id": kakao_id,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(KAKAO_UNLINK_URL, headers=headers, data=data)
        if resp.status_code == 200:
            logger.info("Kakao unlink 성공: kakao_id=%s", kakao_id)
        else:
            # 4xx/5xx — 사용자 탈퇴는 진행하되 무엇이 실패했는지 로그.
            # 자주 보이는 케이스:
            #   401 — 어드민 키 잘못됨 (KAKAO_ADMIN_KEY 재확인)
            #   400 — target_id 형식 오류 또는 이미 unlink 된 사용자
            logger.warning(
                "Kakao unlink 실패: kakao_id=%s status=%d body=%s",
                kakao_id,
                resp.status_code,
                resp.text,
            )
    except Exception as e:
        logger.exception("Kakao unlink 예외: kakao_id=%s err=%s", kakao_id, e)


async def upsert_kakao_user(profile: dict[str, Any], db: AsyncSession) -> User:
    """Find or create a User row keyed by the Kakao numeric ``id``."""
    kakao_id = str(profile.get("id"))
    if not kakao_id or kakao_id == "None":
        raise HTTPException(status_code=400, detail="Kakao profile has no id")

    result = await db.execute(select(User).where(User.kakao_id == kakao_id))
    user = result.scalar_one_or_none()

    kakao_account = profile.get("kakao_account") or {}
    kakao_profile = kakao_account.get("profile") or {}
    nickname = kakao_profile.get("nickname")
    photo_url = kakao_profile.get("profile_image_url")

    if user is None:
        user = User(
            kakao_id=kakao_id,
            nickname=nickname,
            photo_url=photo_url,
        )
        db.add(user)
    else:
        # Refresh nickname/photo from Kakao only if the user hasn't customized
        # them locally — once a user edits their profile we don't want a
        # subsequent login to clobber their changes.
        if user.nickname is None and nickname:
            user.nickname = nickname
        if user.photo_url is None and photo_url:
            user.photo_url = photo_url

    await db.commit()
    await db.refresh(user)
    return user