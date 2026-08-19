"""관리자 재화 원장 (`/admin/wallet`) — ADM-WALLET-001.

**잔액을 직접 수정하는 엔드포인트가 없다.** 쓰기 경로는 지급/회수 하나(`/adjust`)뿐이고,
그것도 "얼마를 움직인다"만 받는다. 잔액은 원장이 계산한다 —
`tests/test_admin_wallet.py::test_no_endpoint_can_set_balance_directly` 가 이 사실을
라우팅 수준에서 고정한다.

조회는 Viewer 도 한다(`WALLET_READ`). 지급·회수는 Super Admin 만이다(`WALLET_WRITE`).
금전 가치가 움직이므로 사유가 필수이고, 반영되지 않은 경우에도 감사 로그는 남긴다(B-2).
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_permission
from app.core.admin_rbac import WALLET_READ, WALLET_WRITE
from app.database import get_db
from app.models.admin import AdminUser
from app.schemas.admin_wallet import (
    AdjustRequest,
    AdjustResponse,
    WalletDetail,
    WalletListResponse,
)
from app.services import admin_wallet
from app.services.audit import record_admin_action
from app.services.rate_limit import client_ip

router = APIRouter()

_MENU = "재화"
_TARGET = "user"

_MAX_PAGE_SIZE = 100


@router.get("", response_model=WalletListResponse)
async def list_wallets(
    q: str | None = Query(default=None, max_length=64),
    mismatch: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(WALLET_READ)),
    db: AsyncSession = Depends(get_db),
):
    """회원별 재화 현황. 잔액이 원장 합계와 다른 회원이 맨 위로 온다.

    불일치 건수는 **필터와 무관하게 전체 기준**으로 함께 내려간다. 필터를 건 뒤에 세면
    "불일치 0건"이 필터의 결과인지 사실인지 화면에서 구분할 수 없다(결제 화면과 같다).
    """
    total, items = await admin_wallet.list_wallets(
        db, q=q, mismatch_only=mismatch, page=page, size=size
    )
    return WalletListResponse(
        total=total,
        page=page,
        size=size,
        items=items,
        mismatch_total=await admin_wallet.mismatch_total(db),
        entry_types=list(admin_wallet.ENTRY_TYPES),
    )


@router.get("/{user_id}", response_model=WalletDetail)
async def get_wallet(
    user_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(WALLET_READ)),
    db: AsyncSession = Depends(get_db),
):
    """회원 한 명의 원장. 전잔액/후잔액과 불일치 검증 결과를 함께 준다."""
    user = await admin_wallet.load_member(db, user_id)
    return await admin_wallet.build_detail(db, user, page=page, size=size)


@router.post("/{user_id}/adjust", response_model=AdjustResponse)
async def adjust_wallet(
    user_id: int,
    data: AdjustRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission(WALLET_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """운영 보상 지급 · 회수.

    **두 번째 클릭은 실패가 아니라 `applied=false` 다.** 같은 `request_id` 가 왔다는
    사실을 그대로 알려 준다 — 오류로 돌려주면 운영자가 "안 됐나 보다" 하고 또 누른다.
    """
    user = await admin_wallet.load_member(db, user_id)
    before_balance = user.star_balance
    applied, message = await admin_wallet.adjust(
        db,
        user,
        direction=data.direction,
        amount=data.amount,
        request_id=data.request_id,
    )

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=user.id,
        action="지급" if data.direction == "grant" else "회수",
        before={"star_balance": before_balance},
        after={
            "star_balance": user.star_balance,
            "applied": applied,
            "amount": data.amount,
            "request_id": data.request_id,
        },
        reason=data.reason,
        ip=client_ip(request),
    )

    return AdjustResponse(
        applied=applied,
        amount=data.amount,
        message=message,
        detail=await admin_wallet.build_detail(db, user, page=1, size=20),
    )
