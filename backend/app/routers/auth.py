"""카카오 OAuth 로그인 시작 및 콜백 처리 엔드포인트."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    AppLoginCodeExchange,
    LoginRequest,
    LoginResponse,
    ReauthRequest,
)
from app.services.account_status import assert_usable
from app.services.app_login_code import (
    CODE_CHALLENGE_RE,
    issue_login_code,
    redeem_login_code,
)
from app.services.rate_limit import (
    check_daily_limit,
    client_ip,
    daily_attempt_count,
    record_daily_attempt,
)
from app.services.auth import (
    exchange_code_for_token,
    fetch_kakao_profile,
    kakao_authorize_url,
    upsert_kakao_user,
)

router = APIRouter()

# 본인 확인 시도 상한(1인 1일). 비밀번호를 아는 사람이 하루에 이만큼 재확인할 일은 없고,
# 탈취한 토큰으로 비밀번호를 캐내려는 시도는 여기서 막힌다.
_REAUTH_DAILY_LIMIT = 30

# 비밀번호 로그인 실패 상한(1일). **실패한 시도만 센다** — 정상 로그인은 아무리 자주
# 해도 잠기지 않는다. 아이디 기준은 계정 하나를 노리는 브루트포스를, 출처 IP 기준은
# 아이디 목록을 훑는 크리덴셜 스터핑을 막는다. IP 쪽 한도를 넉넉히 잡은 이유는
# 국내 이동통신 CGNAT 뒤에서 여러 사용자가 같은 IP 로 보이기 때문이다.
_LOGIN_FAIL_ACTION = "login_fail"
_LOGIN_FAIL_DAILY_LIMIT_PER_USERNAME = 10
_LOGIN_FAIL_DAILY_LIMIT_PER_IP = 100

# 번들 앱은 https://localhost 에서 돌아가 백엔드가 리다이렉트로 되돌려줄 수 없다.
# 대신 커스텀 스킴으로 되돌리면 OS 가 앱을 깨워 준다(Capacitor appUrlOpen).
_APP_URL_SCHEME = "com.melobe.app"
_APP_STATE = "app"
# 앱 로그인의 state 는 `app.<code_challenge>` 형태다. 챌린지를 카카오를 거쳐
# 콜백까지 그대로 들고 오기 위한 것으로, state 는 카카오가 되돌려주는 값이라
# 서버가 따로 보관할 필요가 없다.
_APP_STATE_PREFIX = f"{_APP_STATE}."


@router.get("/kakao")
async def kakao_login(platform: str | None = None, code_challenge: str | None = None):
    """Step 1: redirect the user to Kakao's consent page.

    네이티브 앱은 ``?platform=app&code_challenge=…`` 으로 진입한다. 이 값은 state 로
    카카오를 거쳐 콜백까지 전달되어, 로그인 완료 후 어디로 돌려보낼지와 발급한
    1회용 코드를 누구에게 내줄지를 결정한다.
    """
    if platform != _APP_STATE:
        return RedirectResponse(url=kakao_authorize_url(None), status_code=302)

    if code_challenge is None or not CODE_CHALLENGE_RE.match(code_challenge):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="앱 로그인에는 code_challenge 가 필요해요. 앱을 최신 버전으로 업데이트해주세요.",
        )
    return RedirectResponse(
        url=kakao_authorize_url(f"{_APP_STATE_PREFIX}{code_challenge}"),
        status_code=302,
    )


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
    # 정지된 회원에게는 토큰도 1회용 코드도 내주지 않는다 (T-E10). 카카오 인증 자체는
    # 이미 끝난 뒤라, 여기서 막지 않으면 카카오 로그인만으로 정지가 무력화된다.
    assert_usable(user)
    is_new = user.birth_date is None

    # 앱: 딥링크에는 1회용 코드만 싣는다. 커스텀 스킴은 가로챌 수 있어 토큰을 실을 수 없다.
    if state is not None and state.startswith(_APP_STATE_PREFIX):
        login_code = await issue_login_code(
            user.id, is_new, state[len(_APP_STATE_PREFIX) :]
        )
        return RedirectResponse(
            url=f"{_APP_URL_SCHEME}://auth/callback?code={login_code}", status_code=302
        )

    jwt_token = create_access_token(user.id)
    query = f"?token={jwt_token}&is_new={'1' if is_new else '0'}"
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback{query}", status_code=302)


@router.post("/app/exchange", response_model=LoginResponse)
async def exchange_app_login_code(
    data: AppLoginCodeExchange, db: AsyncSession = Depends(get_db)
):
    """Step 3(앱 전용): 딥링크로 받은 1회용 코드를 실제 토큰으로 바꾼다.

    코드는 한 번만 통하고, 로그인을 시작한 앱만 아는 code_verifier 가 맞아야 한다.
    """
    session = await redeem_login_code(data.code, data.code_verifier)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="로그인 정보가 만료되었거나 올바르지 않아요. 다시 로그인해주세요.",
        )
    user_id, is_new = session
    # 코드는 2분간 유효하다. 그 사이에 상태가 바뀔 수 있으니 발급 직전에 다시 본다.
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="로그인 정보가 만료되었거나 올바르지 않아요. 다시 로그인해주세요.",
        )
    assert_usable(user)
    return LoginResponse(token=create_access_token(user_id), is_new=is_new)


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """아이디/비밀번호 로그인 — 온보딩에서 설정한 자격으로 토큰을 발급한다.

    상한을 **비밀번호 검증보다 먼저** 본다. 검증 뒤에 보면 비밀번호를 맞힌 순간
    통과해 버려 브루트포스를 못 막는다.
    """
    ip = client_ip(request)
    username_subject = f"username:{data.username}"
    ip_subject = f"ip:{ip}"
    if (
        await daily_attempt_count(username_subject, _LOGIN_FAIL_ACTION)
        >= _LOGIN_FAIL_DAILY_LIMIT_PER_USERNAME
        or await daily_attempt_count(ip_subject, _LOGIN_FAIL_ACTION)
        >= _LOGIN_FAIL_DAILY_LIMIT_PER_IP
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="로그인 시도가 너무 많아요. 잠시 후 다시 시도해주세요.",
        )

    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.password_hash
        or not verify_password(data.password, user.password_hash)
    ):
        await record_daily_attempt(username_subject, _LOGIN_FAIL_ACTION)
        await record_daily_attempt(ip_subject, _LOGIN_FAIL_ACTION)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않아요.",
        )
    # 비밀번호 검증 뒤에 본다. 앞에 두면 아이디만으로 계정 상태를 캘 수 있다.
    assert_usable(user)
    return LoginResponse(
        token=create_access_token(user.id),
        is_new=user.birth_date is None,
    )


@router.post("/reauth", response_model=LoginResponse)
async def reauth(data: ReauthRequest, current_user: User = Depends(get_current_user)):
    """민감 액션 앞의 비밀번호 재확인 — 통과하면 인증 시각이 지금인 새 토큰을 준다.

    카카오로만 가입해 비밀번호가 없는 계정은 카카오 재로그인이 재인증 경로다.
    비밀번호 불일치를 401 이 아니라 400 으로 돌려주는 이유: 401 이면 클라이언트가
    로그인이 풀린 줄 알고 토큰을 버린다. 세션은 멀쩡하고 확인만 실패한 것이다.
    """
    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="비밀번호가 설정되어 있지 않아요. 카카오로 다시 로그인해주세요.",
        )

    if not await check_daily_limit(current_user.id, "reauth", _REAUTH_DAILY_LIMIT):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="본인 확인 시도가 너무 많아요. 잠시 후 다시 시도해주세요.",
        )

    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호가 올바르지 않아요.",
        )

    return LoginResponse(token=create_access_token(current_user.id), is_new=False)
