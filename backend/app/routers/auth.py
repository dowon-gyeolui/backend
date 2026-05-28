"""카카오 OAuth 2.0 진입점.

플로우:
  GET /auth/kakao           — 카카오 동의 페이지로 302 리다이렉트
  GET /auth/kakao/callback  — 카카오가 code 파라미터로 콜백.
                              code → access_token → 프로필 조회 →
                              User upsert → JWT 발급 후 프론트로
                              `${frontend_url}/auth/callback?token=...&is_new=...`
                              로 302.

is_new 플래그는 birth_date 가 NULL 인지로 판정해, 프론트가 신규
사용자에게 온보딩 페이지를, 기존 사용자에게 홈을 즉시 보여줄 수
있게 한다.
"""
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

    # Tell the SPA whether this is a brand-new user (needs onboarding) or a
    # returning one (go straight to home). is_new = True iff birth_date is
    # still NULL — that's the first onboarding step.
    is_new = user.birth_date is None

    redirect = (
        f"{settings.frontend_url}/auth/callback"
        f"?token={jwt_token}&is_new={'1' if is_new else '0'}"
    )
    return RedirectResponse(url=redirect, status_code=302)