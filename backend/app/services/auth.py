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