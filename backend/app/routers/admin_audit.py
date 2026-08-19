"""관리자 감사 로그 (`/admin/audit`) — ADM-LOG-001.

**GET 두 개뿐이다.** 감사 로그를 만들거나 고치거나 지우는 엔드포인트는 없다
(QA: "로그 삭제 기능 금지"). 다른 화면들이 "쓰기 경로를 하나로 줄였다"면 여기는
아예 없앴고, `tests/test_admin_audit.py::test_router_exposes_read_only_endpoints`
가 라우팅 수준에서 그 사실을 고정한다.

Super Admin 전용이다(`AUDIT_READ`). Viewer 에게 주지 않는 이유는 이 화면이
"누가 무엇을 봤는지"까지 담고 있어, 조회 권한만 있는 계정이 다른 관리자의 활동을
들여다보는 통로가 되기 때문이다.

**이 화면의 조회 자체는 감사 로그에 남기지 않는다.** B-2 가 "바꾼 것은 전부, 본 것은
민감정보만" 으로 정했고, 여기 나오는 값은 이미 마스킹된 것들이다. 감사 로그 조회를
감사 로그에 남기면 로그가 자기 자신으로 가득 찬다.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_permission
from app.core.admin_rbac import AUDIT_READ
from app.database import get_db
from app.models.admin import AdminUser
from app.schemas.admin_audit import AuditListResponse, AuditLogDetail
from app.services import admin_audit

router = APIRouter()

_NOT_FOUND = "존재하지 않는 감사 로그예요."

_MAX_PAGE_SIZE = 100


@router.get("", response_model=AuditListResponse)
async def list_audit_logs(
    q: str | None = Query(default=None, max_length=100),
    menu: str | None = Query(default=None, max_length=50),
    action: str | None = Query(default=None, max_length=50),
    admin_id: int | None = Query(default=None, ge=0),
    target_type: str | None = Query(default=None, max_length=50),
    target_id: str | None = Query(default=None, max_length=64),
    day_from: date | None = Query(default=None, alias="from"),
    day_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    """감사 로그 목록. 최신순 고정이고, 날짜 필터는 KST 기준이다.

    `admin_id=0` 은 **없는 계정으로 들어온 로그인 실패**를 고르는 값이다. `ge=0` 인
    것은 그래서다 — 그 행을 못 고르게 하면 계정 열거 시도를 화면에서 찾을 수 없다.
    """
    return await admin_audit.list_logs(
        db,
        q=q,
        menu=menu,
        action=action,
        admin_id=admin_id,
        target_type=target_type,
        target_id=target_id,
        day_from=day_from,
        day_to=day_to,
        page=page,
        size=size,
    )


@router.get("/{log_id}", response_model=AuditLogDetail)
async def get_audit_log(
    log_id: int,
    _: AdminUser = Depends(require_permission(AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    """로그 한 건과 변경 전후값 diff."""
    detail = await admin_audit.get_log(db, log_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return detail
