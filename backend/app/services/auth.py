"""Kakao OAuth 2.0 helpers.

Two HTTP calls are needed against Kakao's API:

1. ``POST https://kauth.kakao.com/oauth/token`` — exchange the ``code`` Kakao
   gives our redirect URI for an ``access_token``.
2. ``GET  https://kapi.kakao.com/v2/user/me`` — fetch the user's profile so we
   can grab a stable ``id`` (their kakao_id) and any nickname/photo.

Errors from Kakao are surfaced as ``HTTPException(400)`` so the caller's
redirect handler can display something reasonable instead of a generic 500.
"""
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"
KAKAO_UNLINK_URL = "https://kapi.kakao.com/v1/user/unlink"


def kakao_authorize_url() -> str:
    """The Kakao consent page URL we redirect the user to from /auth/kakao."""
    params = {
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
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
    target_id_type=user_id 방식으로 unlink 가능. 키가 없으면 best-effort
    로 스킵하고 경고 로그만 (로컬 dev 환경 보호).

    Kakao 가 4xx/5xx 를 돌려줘도 우리 쪽 탈퇴는 진행한다 — 사용자가
    이미 다른 데서 unlink 했거나 Kakao 일시 장애 일 수 있고, 그렇다고
    해서 우리 DB 의 탈퇴 자체가 막혀선 안 됨.
    """
    if not settings.kakao_admin_key:
        # Local dev or misconfigured prod — log and skip rather than fail.
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
            await client.post(KAKAO_UNLINK_URL, headers=headers, data=data)
    except Exception:
        # Network error / Kakao downtime — swallow so the user-facing
        # delete still succeeds. Strikes/photos/etc. are already wiped
        # by the time we reach this call.
        pass


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