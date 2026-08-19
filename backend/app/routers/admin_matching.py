"""관리자 매칭 대상자·후보 검증 (`/admin/matching`) — ADM-MATCH-001/002.

QA 문서가 "가장 중요한 화면"으로 지목한 곳이다. 여기서 답해야 하는 질문은 두 개뿐이다.

1. **왜 이 회원에게 추천이 안 나가는가** — 대상자 목록이 문제 있는 회원을 먼저 보여준다.
2. **왜 이 후보가 빠졌는가 / 왜 들어왔는가** — 후보 검증이 조건 코드와 값으로 답한다.

조회는 Viewer 도 한다. 재계산만 Super Admin 이다 — 상태를 바꾸지는 않지만 회원 수만큼
조건을 다시 판정하는 비용이 드는 작업이고, 감사 로그 대상 표(B-2)가 "남용 추적"을
이유로 이 액션을 지목했다. 조회만 가능한 역할이 비용을 유발하는 버튼을 갖지 않게 한다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_permission
from app.core.admin_rbac import MATCH_READ, MATCH_WRITE
from app.database import get_db
from app.models.admin import AdminUser
from app.models.user import User
from app.schemas.admin_matching import (
    RecalculateRequest,
    TargetListResponse,
    TargetState,
    TargetVerification,
)
from app.services import admin_matching
from app.services.audit import record_admin_action
from app.services.rate_limit import client_ip

router = APIRouter()

_MENU = "매칭"
_TARGET = "user"
_NOT_FOUND = "존재하지 않거나 탈퇴한 회원이에요."

_MAX_PAGE_SIZE = 100


async def _get_member(member_id: int, db: AsyncSession) -> User:
    user = await db.get(User, member_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return user


@router.get("", response_model=TargetListResponse)
async def list_targets(
    q: str | None = Query(default=None, max_length=50),
    state: TargetState | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(MATCH_READ)),
    db: AsyncSession = Depends(get_db),
):
    """매칭 대상자 목록. 정렬은 고정이다 — 오류 · 후보 없음 · 정상 순.

    화면에서 정렬을 바꿀 수 있게 하지 않는다. 이 목록의 쓸모는 "문제를 먼저 보여주는
    것"이고, 가입일순으로 볼 일이 있으면 회원 목록(ADM-MEM-001)이 이미 그렇게 준다.
    """
    total, items = await admin_matching.list_targets(
        db, q=q, state=state, page=page, size=size
    )
    return TargetListResponse(total=total, page=page, size=size, items=items)


@router.get("/{member_id}", response_model=TargetVerification)
async def verify_target(
    member_id: int,
    _: AdminUser = Depends(require_permission(MATCH_READ)),
    db: AsyncSession = Depends(get_db),
):
    """후보 검증 — 조건 스냅샷, 후보별 통과 근거, 제외 후보와 최초 탈락 조건."""
    return await admin_matching.verify(db, await _get_member(member_id, db))


@router.post("/{member_id}/recalculate", response_model=TargetVerification)
async def recalculate_target(
    member_id: int,
    data: RecalculateRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission(MATCH_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """후보 재계산 요청 (ADM-MATCH-001).

    **후보는 저장되지 않는다.** 이 앱은 카드를 발급하는 순간에 후보군을 계산하므로
    (`services/matching._next_candidate`), 여기서 무효화할 캐시가 없다. 그래서 재계산은
    "지금 다시 판정한 결과"를 그대로 돌려주는 것과 같고, 조회와 다른 점은 **누가 왜
    돌렸는지가 감사 로그에 남는다**는 것뿐이다. 나중에 후보군을 미리 계산해 두게 되면
    그 무효화를 붙일 자리도 여기다.
    """
    user = await _get_member(member_id, db)
    result = await admin_matching.verify(db, user)

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=user.id,
        action="후보재계산",
        after={
            "state": result.target.state,
            "pool_count": result.pool_count,
            "available_count": result.available_count,
        },
        reason=data.reason,
        ip=client_ip(request),
    )
    return result
