"""관리자 회원 목록·상세 (`/admin/members`) — ADM-MEM-001/002.

권한은 화면이 아니라 여기서 갈린다(`require_permission`). 목록·상세는 Viewer 도 보고,
상태 변경과 프로필 노출 중단은 Super Admin 만, 민감정보 원문은 사유를 받은 Super Admin
만 본다(OI-MEM-003/004).

**탈퇴 회원은 이 API 에 없다.** 탈퇴는 행을 지우는 hard delete 라(PIPA,
`services/users.delete_account`) 목록에 나오지 않고 상세는 404 다. 그래서 상태 필터에도
"탈퇴"가 없다 — 없는 상태를 고를 수 있게 두면 항상 0건인 필터가 된다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_permission
from app.core.admin_rbac import MEMBER_READ, MEMBER_UNMASK, MEMBER_WRITE
from app.database import get_db
from app.models.admin import AdminUser
from app.models.user import User
from app.schemas.admin_member import (
    MemberDetail,
    MemberListResponse,
    MemberStatus,
    MemberStatusUpdate,
    MemberSummary,
    MemberUnmaskRequest,
    MemberUnmaskResponse,
    MemberVisibilityUpdate,
)
from app.services import admin_members
from app.services.audit import record_admin_action
from app.services.rate_limit import client_ip

router = APIRouter()

_MENU = "회원"
_TARGET = "user"
_NOT_FOUND = "존재하지 않거나 탈퇴한 회원이에요."

_MAX_PAGE_SIZE = 100


async def _get_member(member_id: int, db: AsyncSession) -> User:
    user = await db.get(User, member_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return user


@router.get("", response_model=MemberListResponse)
async def list_members(
    q: str | None = Query(default=None, max_length=50),
    member_status: MemberStatus | None = Query(default=None, alias="status"),
    matchable: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(MEMBER_READ)),
    db: AsyncSession = Depends(get_db),
):
    """회원 목록. 가입일 내림차순 고정, 연락처·생년월일은 마스킹된 값으로 나간다.

    조회는 감사 로그에 남기지 않는다(B-2). 목록 조회까지 남기면 로그가 폭증해
    정작 중요한 기록을 못 찾는다 — 민감정보 원문 열람은 별도 엔드포인트가 남긴다.
    """
    total, items = await admin_members.list_members(
        db, q=q, status=member_status, matchable=matchable, page=page, size=size
    )
    return MemberListResponse(total=total, page=page, size=size, items=items)


@router.get("/{member_id}", response_model=MemberDetail)
async def get_member(
    member_id: int,
    _: AdminUser = Depends(require_permission(MEMBER_READ)),
    db: AsyncSession = Depends(get_db),
):
    return await admin_members.build_detail(db, await _get_member(member_id, db))


@router.post("/{member_id}/status", response_model=MemberSummary)
async def update_status(
    member_id: int,
    data: MemberStatusUpdate,
    request: Request,
    admin: AdminUser = Depends(require_permission(MEMBER_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """회원 상태 변경 (정상/비활성/차단).

    탈퇴로는 바꿀 수 없다. 탈퇴는 되돌릴 수 없고(OI-MEM-001) 개인정보를 실제로 지우는
    작업이라 관리자 버튼 하나로 일어날 일이 아니다 — 스키마 단계에서 값 자체가 없다.
    """
    user = await _get_member(member_id, db)
    before = user.status
    if before != data.status:
        user.status = data.status
        await db.commit()
        await db.refresh(user)

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=user.id,
        action="상태변경",
        before={"status": before},
        after={"status": user.status},
        reason=data.reason,
        ip=client_ip(request),
    )
    return (await admin_members.build_detail(db, user)).summary


@router.post("/{member_id}/profile-visibility", response_model=MemberSummary)
async def update_profile_visibility(
    member_id: int,
    data: MemberVisibilityUpdate,
    request: Request,
    admin: AdminUser = Depends(require_permission(MEMBER_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """프로필 노출 중단·재개. 상태와 따로 두는 이유는 모델 주석에 있다."""
    user = await _get_member(member_id, db)
    before = user.profile_hidden
    if before != data.hidden:
        user.profile_hidden = data.hidden
        await db.commit()
        await db.refresh(user)

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=user.id,
        action="프로필노출중단" if data.hidden else "프로필노출재개",
        before={"profile_hidden": before},
        after={"profile_hidden": user.profile_hidden},
        reason=data.reason,
        ip=client_ip(request),
    )
    return (await admin_members.build_detail(db, user)).summary


@router.post("/{member_id}/unmask", response_model=MemberUnmaskResponse)
async def unmask_member(
    member_id: int,
    data: MemberUnmaskRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission(MEMBER_UNMASK)),
    db: AsyncSession = Depends(get_db),
):
    """민감정보 원문 열람 — 사유 필수 · Super Admin 한정 · 열람 로그 (OI-MEM-004).

    본 것을 남기는 유일한 조회다. 로그에는 **무엇을 봤는지만** 남기고 본 값은 남기지
    않는다 — 열람 기록을 뒤지면 원문이 나오는 로그는 그 자체가 두 번째 유출 경로다.
    """
    user = await _get_member(member_id, db)

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=user.id,
        action="민감정보열람",
        after={"fields": ["kakao_id", "username", "birth_date", "birth_time", "birth_place"]},
        reason=data.reason,
        ip=client_ip(request),
    )

    return MemberUnmaskResponse(
        id=user.id,
        kakao_id=user.kakao_id,
        username=user.username,
        birth_date=None if user.birth_date is None else user.birth_date.isoformat(),
        birth_time=user.birth_time,
        birth_place=user.birth_place,
    )
