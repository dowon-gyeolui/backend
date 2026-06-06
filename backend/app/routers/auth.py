from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token
from app.database import get_db
from app.services.auth import (
    exchange_code_for_token,
    fetch_kakao_profile,
    kakao_authorize_url,
    upsert_kakao_user,
)

router = APIRouter()


@router.get("/kakao")
async def kakao_login():
    """Step 1: redirect the user to Kakao's consent page."""
    return RedirectResponse(url=kakao_authorize_url(), status_code=302)


@router.get("/kakao/callback")
async def kakao_callback(code: str, db: AsyncSession = Depends(get_db)):
    """Step 2: Kakao redirected back with ``?code=…``. Finish the dance."""
    access_token = await exchange_code_for_token(code)
    profile = await fetch_kakao_profile(access_token)
    user = await upsert_kakao_user(profile, db)
    jwt_token = create_access_token(user.id)

    is_new = user.birth_date is None

    redirect = (
        f"{settings.frontend_url}/auth/callback"
        f"?token={jwt_token}&is_new={'1' if is_new else '0'}"
    )
    return RedirectResponse(url=redirect, status_code=302)