"""관리자 인증과 계정 자기관리 (`/admin`). 권한 판정은 `core/admin_deps` 가 한다.

여기에는 회원·결제·매칭 화면이 없다 — T-E03 이후가 이 라우터의 권한 의존성 위에
얹는다. 이 파일이 책임지는 것은 "누가 관리자인가"와 "그 세션으로 무엇을 할 수 있는가"
둘뿐이다.

계정 열거를 막는 규칙이 이 파일 전체에 걸린다: 없는 이메일 · 틀린 비밀번호 ·
비활성 계정이 **모두 같은 문구와 같은 상태코드**로 답한다. 하나라도 다르면 로그인
화면이 곧 "이 이메일이 관리자인지" 조회 API 가 된다(ADM-AUTH-F004).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import get_current_admin, require_permission
from app.core.admin_rbac import ADMIN_READ, permissions_for
from app.core.security import create_admin_access_token, hash_password, verify_password
from app.database import get_db
from app.models.admin import AdminUser
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminMeResponse,
    AdminPasswordChangeRequest,
    AdminSummary,
)
from app.services.audit import record_admin_action
from app.services.rate_limit import client_ip, daily_attempt_count, record_daily_attempt

router = APIRouter()

_MENU_AUTH = "관리자인증"
_MENU_ACCOUNT = "관리자계정"
_TARGET_ADMIN = "admin"

# 로그인 실패 상한(1일). **실패한 시도만 센다** — 정상 로그인은 아무리 자주 해도 잠기지
# 않는다. 관리자는 세 명뿐이라 앱 사용자보다 좁게 잡는다. IP 쪽이 넓은 것은 사무실
# 공용 회선 뒤에서 세 명이 같은 IP 로 보이기 때문이다.
_LOGIN_FAIL_ACTION = "admin_login_fail"
_LOGIN_FAIL_DAILY_LIMIT_PER_EMAIL = 5
_LOGIN_FAIL_DAILY_LIMIT_PER_IP = 30

# 감사 로그의 `admin_id` 는 NOT NULL 이다. 존재하지 않는 이메일로 들어온 실패는
# 가리킬 계정이 없으므로 이 값으로 남긴다(관리자 id 는 1부터 시작한다).
_UNKNOWN_ADMIN_ID = 0

_LOGIN_FAILED = "이메일 또는 비밀번호가 올바르지 않아요."


def _me(admin: AdminUser) -> AdminMeResponse:
    return AdminMeResponse(
        id=admin.id,
        email=admin.email,
        name=admin.name,
        role=admin.role,
        must_change_password=admin.must_change_password,
        permissions=sorted(permissions_for(admin.role)),
    )


@router.post("/auth/login", response_model=AdminLoginResponse)
async def admin_login(
    data: AdminLoginRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """이메일/비밀번호 로그인 (로그인 식별자는 이메일로 확정, 2026-08-19).

    상한을 **비밀번호 검증보다 먼저** 본다. 검증 뒤에 보면 비밀번호를 맞힌 순간
    통과해 버려 브루트포스를 막지 못한다.
    """
    ip = client_ip(request)
    email = data.email.strip().lower()  # 계정은 소문자 이메일로 저장한다
    email_subject = f"admin-email:{email}"
    ip_subject = f"admin-ip:{ip}"

    if (
        await daily_attempt_count(email_subject, _LOGIN_FAIL_ACTION)
        >= _LOGIN_FAIL_DAILY_LIMIT_PER_EMAIL
        or await daily_attempt_count(ip_subject, _LOGIN_FAIL_ACTION)
        >= _LOGIN_FAIL_DAILY_LIMIT_PER_IP
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="로그인 시도가 너무 많아요. 잠시 후 다시 시도해주세요.",
        )

    result = await db.execute(select(AdminUser).where(AdminUser.email == email))
    admin = result.scalar_one_or_none()

    if admin is None or not admin.is_active or not verify_password(
        data.password, admin.password_hash
    ):
        await record_daily_attempt(email_subject, _LOGIN_FAIL_ACTION)
        await record_daily_attempt(ip_subject, _LOGIN_FAIL_ACTION)
        await record_admin_action(
            admin_id=_UNKNOWN_ADMIN_ID if admin is None else admin.id,
            menu=_MENU_AUTH,
            target_type=_TARGET_ADMIN,
            target_id=None if admin is None else admin.id,
            action="로그인실패",
            after={"email": email},  # 저장 직전에 마스킹된다(services/audit)
            ip=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED
        )

    admin.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU_AUTH,
        target_type=_TARGET_ADMIN,
        target_id=admin.id,
        action="로그인",
        ip=ip,
    )

    return AdminLoginResponse(
        token=create_admin_access_token(admin.id),
        name=admin.name,
        role=admin.role,
        must_change_password=admin.must_change_password,
        permissions=sorted(permissions_for(admin.role)),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout(request: Request, admin: AdminUser = Depends(get_current_admin)):
    """로그아웃을 기록한다. 토큰 폐기는 클라이언트가 한다.

    토큰은 상태를 서버에 두지 않는 JWT 라 서버가 즉시 무효화할 수 없다. 대신 유휴
    2시간 만료가 상한이고, 이 기록이 "언제 자리를 떠났는가"를 남긴다(감사 로그 대상 목록).
    """
    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU_AUTH,
        target_type=_TARGET_ADMIN,
        target_id=admin.id,
        action="로그아웃",
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AdminMeResponse)
async def admin_me(admin: AdminUser = Depends(get_current_admin)):
    """내 계정과 권한 목록. 관리자 앱이 메뉴를 그릴 때 쓴다.

    이 목록은 화면을 그리기 위한 것이지 통제 수단이 아니다. 실제 통제는 각 엔드포인트의
    `require_permission` 이 한다.
    """
    return _me(admin)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    data: AdminPasswordChangeRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """자기 비밀번호 변경. 최초 로그인에서 부트스트랩 값을 벗는 유일한 경로다(D-8).

    `require_permission` 이 아니라 `get_current_admin` 에만 기댄다 —
    `must_change_password` 인 관리자가 통과해야 하는 문이라서 권한 검사 뒤에 두면
    영원히 비밀번호를 바꿀 수 없다.
    """
    if not verify_password(data.current_password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="현재 비밀번호가 올바르지 않아요.",
        )
    if data.new_password == data.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호는 현재 비밀번호와 달라야 해요.",
        )

    admin.password_hash = hash_password(data.new_password)
    admin.must_change_password = False
    await db.commit()

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU_ACCOUNT,
        target_type=_TARGET_ADMIN,
        target_id=admin.id,
        action="비밀번호변경",
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admins", response_model=list[AdminSummary])
async def list_admins(
    _: AdminUser = Depends(require_permission(ADMIN_READ)),
    db: AsyncSession = Depends(get_db),
):
    """관리자 계정 목록 — Super Admin 전용.

    "누가 이 시스템에 들어올 수 있는가"는 Viewer 에게 보여줄 이유가 없는 정보다.
    비밀번호 해시는 응답 스키마에 아예 없다.
    """
    result = await db.execute(select(AdminUser).order_by(AdminUser.id))
    return [AdminSummary.model_validate(row) for row in result.scalars()]
