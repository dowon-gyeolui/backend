"""현재 로그인 유저를 요청에서 추출하는 인증 의존성(get_current_user)."""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.request_context import current_user_id
from app.core.security import SCOPE_USER, create_access_token, decode_token_claims
from app.database import get_db
from app.models.user import User

# 갱신 토큰을 실어 보내는 응답 헤더. 클라이언트는 이 값이 오면 저장 중인 토큰을 갈아끼운다.
REFRESHED_TOKEN_HEADER = "X-Refreshed-Token"
# 민감 액션이 재인증을 요구할 때 붙이는 표식. 클라이언트가 "권한 없음"과 구분하는 근거.
REAUTH_REQUIRED_HEADER = "X-Reauth-Required"

# 토큰이 이보다 오래됐을 때만 새로 발급한다. 매 요청 재발급은 낭비고, 너무 길게 잡으면
# 유휴 만료(2시간) 직전에 갱신 기회를 놓친다.
_REFRESH_AFTER = timedelta(minutes=5)


async def get_current_user(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_dev_user_id: int | None = Header(default=None, alias="X-Dev-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims = decode_token_claims(token)
        if claims is None or claims.scope != SCOPE_USER:
            # 관리자 토큰(`scope="admin"`)은 여기서 끊는다. 두 테이블의 id 공간이 겹쳐
            # 그대로 통과시키면 관리자 id 와 같은 번호의 사용자로 로그인된다.
            raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")

        now = datetime.now(timezone.utc)
        if claims.session_expires_at <= now:
            # 유휴 만료는 exp 로 이미 걸렸다. 여기는 갱신을 반복해 온 세션의 절대 상한.
            raise HTTPException(status_code=401, detail="세션이 만료되었어요. 다시 로그인해주세요.")

        # DB 조회보다 먼저 건다. 토큰은 서명되어 있어 여기서의 user_id 는 이미 신뢰할 수
        # 있고, 바로 아래 db.get 이 여는 트랜잭션부터 세션 변수가 붙어야 한다.
        current_user_id.set(claims.user_id)

        user = await db.get(User, claims.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="유저를 찾을 수 없습니다.")

        # 민감 액션 재인증 판정(assert_recent_auth)이 읽는다.
        request.state.auth_time = claims.auth_time

        # 슬라이딩 갱신 — 활동이 있었으니 유휴 시계를 다시 감는다. auth_time 은 그대로
        # 넘겨 세션의 절대 상한과 재인증 시각을 잇는다.
        # 엔드포인트가 Response 객체를 직접 반환하면(204 등) 이 헤더는 실리지 않는다.
        # 그런 응답은 다음 요청에서 갱신되므로 유휴 시계에는 문제가 없다.
        if now - claims.issued_at >= _REFRESH_AFTER:
            response.headers[REFRESHED_TOKEN_HEADER] = create_access_token(
                user.id, auth_time=claims.auth_time
            )
        return user

    # 개발용 무인증 우회. 세 조건을 모두 만족해야 열린다.
    #   - DEBUG, ALLOW_DEV_AUTH 둘 다 켜져 있을 것 (환경변수 하나를 빠뜨려도 안 열린다)
    #   - X-Dev-User-Id 를 **명시적으로** 보냈을 것
    # 예전에는 헤더가 없으면 조용히 user_id=1 로 계정을 만들어 줬다. 그래서 DEBUG 가
    # 켜진 배포에서는 인증 헤더 없는 요청이 전부 200 이었다(strix HIGH, T-H06).
    if settings.debug and settings.allow_dev_auth and x_dev_user_id is not None:
        dev_kakao_id = f"dev_{x_dev_user_id}"
        result = await db.execute(select(User).where(User.kakao_id == dev_kakao_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(kakao_id=dev_kakao_id)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        current_user_id.set(user.id)
        return user

    raise HTTPException(status_code=401, detail="Authentication required")


def assert_recent_auth(request: Request) -> None:
    """민감 액션 앞에서 "최근에 직접 인증했는가"를 확인한다. (OI-AUTH-002)

    토큰을 갱신해도 `auth_time` 은 로그인 시각 그대로다. 그래서 탈취한 토큰을 계속
    갱신해 온 공격자는 이 문턱을 넘지 못한다. 통과하려면 비밀번호 재확인
    (`POST /auth/reauth`)이나 카카오 재로그인으로 새 세션을 받아야 한다.

    401 이 아니라 403 인 이유: 401 은 클라이언트가 "로그인이 풀렸다"로 읽고 토큰을
    버린다. 여기서 세션은 멀쩡하고 이 액션에만 추가 확인이 필요하다.
    """
    auth_time = getattr(request.state, "auth_time", None)
    if auth_time is None:
        # 토큰 없이 들어온 개발 우회(DEBUG) 경로 — 판정할 인증 시각 자체가 없다.
        return

    window = timedelta(minutes=settings.reauth_window_minutes)
    if datetime.now(timezone.utc) - auth_time <= window:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="보안을 위해 본인 확인이 필요해요. 비밀번호를 다시 확인한 뒤 시도해주세요.",
        headers={REAUTH_REQUIRED_HEADER: "1"},
    )


async def require_recent_auth(
    request: Request, current_user: User = Depends(get_current_user)
) -> User:
    """`get_current_user` + 최근 인증 요구. 민감 액션 엔드포인트가 이걸 쓴다."""
    assert_recent_auth(request)
    return current_user
