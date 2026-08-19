"""관리자 인증·권한 의존성. 앱 사용자 인증(`core/deps.py`)과 의도적으로 갈라 둔다.

두 세계가 서로의 토큰을 받아들이면 한쪽의 결함이 곧 다른 쪽 권한이 된다. 경계는
토큰의 `scope` 클레임이고, 여기서는 `SCOPE_ADMIN` 만 받는다.

`current_user_id` contextvar 는 **일부러 채우지 않는다.** 그 값은 앱 사용자 격리용
DB 세션 변수의 출처(`database.py` 의 `after_begin`)라, 관리자 id 를 넣으면 같은 번호의
앱 사용자로 오인된다. 관리자는 애초에 그 격리의 대상이 아니다.

세션 수명은 앱과 같은 값을 쓴다 — 유휴 2시간(`idle_timeout_minutes`, OI-AUTH-002)에
절대 상한(`access_token_expire_minutes`)이 겹쳐 있고, 활동이 있으면 갱신 토큰이
응답 헤더로 나간다.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_rbac import has_permission
from app.core.deps import REFRESHED_TOKEN_HEADER
from app.core.security import (
    SCOPE_ADMIN,
    create_admin_access_token,
    decode_token_claims,
)
from app.database import get_db
from app.models.admin import AdminUser

# 부트스트랩 비밀번호를 아직 안 바꾼 관리자가 다른 화면을 열려고 할 때 붙는 표식.
# 관리자 앱은 이 표식을 보고 비밀번호 변경 화면으로 보낸다.
PASSWORD_CHANGE_REQUIRED_HEADER = "X-Password-Change-Required"

_REFRESH_AFTER = timedelta(minutes=5)

_INVALID_TOKEN = "관리자 세션이 유효하지 않아요. 다시 로그인해주세요."


async def get_current_admin(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """관리자 토큰을 검증하고 계정을 돌려준다. 권한은 보지 않는다.

    개발용 무인증 우회(`X-Dev-User-Id`)에 해당하는 경로는 두지 않는다. 관리자 화면은
    회원 개인정보와 재화를 다뤄, 환경변수 조합 하나로 열릴 수 있는 문을 만들지 않는다.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    claims = decode_token_claims(authorization.split(" ", 1)[1].strip())
    if claims is None or claims.scope != SCOPE_ADMIN:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    now = datetime.now(timezone.utc)
    if claims.session_expires_at <= now:
        raise HTTPException(status_code=401, detail="세션이 만료되었어요. 다시 로그인해주세요.")

    admin = await db.get(AdminUser, claims.user_id)
    if admin is None or not admin.is_active:
        # 비활성 계정과 없는 계정을 구분해 알리지 않는다. 토큰을 쥔 쪽에게 "이 계정은
        # 존재하지만 잠겼다"를 알려 줄 이유가 없다.
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    request.state.auth_time = claims.auth_time

    if now - claims.issued_at >= _REFRESH_AFTER:
        response.headers[REFRESHED_TOKEN_HEADER] = create_admin_access_token(
            admin.id, auth_time=claims.auth_time
        )
    return admin


def require_permission(permission: str) -> Callable:
    """이 권한이 있어야 통과하는 의존성을 만든다.

    화면이 아니라 엔드포인트에 건다. 관리자 앱이 버튼을 숨기든 말든 URL 을 직접
    두드리면 여기서 403 이 나야 한다(QA 체크포인트: RBAC).
    """

    async def _dependency(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.must_change_password:
            # 부트스트랩 비밀번호로는 아무 화면도 열지 못한다(D-8). 자기 정보 조회와
            # 비밀번호 변경만 `get_current_admin` 만으로 열려 있다.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="최초 로그인이에요. 비밀번호를 변경한 뒤 이용해주세요.",
                headers={PASSWORD_CHANGE_REQUIRED_HEADER: "1"},
            )
        if not has_permission(admin.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업을 수행할 권한이 없어요.",
            )
        return admin

    return _dependency
