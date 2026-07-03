"""카카오 OAuth 2.0 로그인/프로필 조회/unlink 헬퍼."""
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

KAKAO_OAUTH_SCOPES = [
    "profile_nickname",
    "profile_image",
    "account_email",
]


def kakao_authorize_url() -> str:
    params = {
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
        "scope": ",".join(KAKAO_OAUTH_SCOPES),
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://kauth.kakao.com/oauth/authorize?{qs}"


async def exchange_code_for_token(code: str) -> str:
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
            logger.warning(
                "Kakao unlink 실패: kakao_id=%s status=%d body=%s",
                kakao_id,
                resp.status_code,
                resp.text,
            )
    except Exception as e:
        logger.exception("Kakao unlink 예외: kakao_id=%s err=%s", kakao_id, e)


async def upsert_kakao_user(profile: dict[str, Any], db: AsyncSession) -> User:
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
        if user.nickname is None and nickname:
            user.nickname = nickname
        if user.photo_url is None and photo_url:
            user.photo_url = photo_url

    await db.commit()
    await db.refresh(user)
    return user