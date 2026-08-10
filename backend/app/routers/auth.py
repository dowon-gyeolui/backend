"""카카오 OAuth 로그인 시작 및 콜백 처리 엔드포인트."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, LoginResponse
from app.services.auth import (
    exchange_code_for_token,
    fetch_kakao_profile,
    kakao_authorize_url,
    upsert_kakao_user,
)

router = APIRouter()

# 번들 앱은 https://localhost 에서 돌아가 백엔드가 리다이렉트로 되돌려줄 수 없다.
# 대신 커스텀 스킴으로 되돌리면 OS 가 앱을 깨워 준다(Capacitor appUrlOpen).
_APP_URL_SCHEME = "com.zami.app"
_APP_STATE = "app"


@router.get("/kakao")
async def kakao_login(platform: str | None = None):
    """Step 1: redirect the user to Kakao's consent page.

    네이티브 앱은 ``?platform=app`` 으로 진입한다. 이 값은 state 로 카카오를
    거쳐 콜백까지 전달되어, 로그인 완료 후 어디로 돌려보낼지 결정한다.
    """
    state = _APP_STATE if platform == _APP_STATE else None
    return RedirectResponse(url=kakao_authorize_url(state), status_code=302)


@router.get("/kakao/callback")
async def kakao_callback(
    code: str,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Step 2: Kakao redirected back with ``?code=…``. Finish the dance."""
    access_token = await exchange_code_for_token(code)
    profile = await fetch_kakao_profile(access_token)
    user = await upsert_kakao_user(profile, db)
    jwt_token = create_access_token(user.id)

    is_new = user.birth_date is None
    query = f"?token={jwt_token}&is_new={'1' if is_new else '0'}"

    if state == _APP_STATE:
        return RedirectResponse(url=f"{_APP_URL_SCHEME}://auth/callback{query}", status_code=302)
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback{query}", status_code=302)


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """아이디/비밀번호 로그인 — 온보딩에서 설정한 자격으로 토큰을 발급한다."""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.password_hash
        or not verify_password(data.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않아요.",
        )
    return LoginResponse(
        token=create_access_token(user.id),
        is_new=user.birth_date is None,
    )