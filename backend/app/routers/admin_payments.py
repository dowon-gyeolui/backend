"""관리자 결제 목록·상세 (`/admin/payments`) — ADM-PAY-001/002.

**이 화면에서 결제를 만들거나 고칠 수 없다.** 결제 성공 여부와 금액의 원천은 결제사이고
(OI-PAY-001), 여기 있는 쓰기 엔드포인트는 지급 재처리 하나뿐이다. 그것도 결제사에 물어
성공과 금액 일치를 확인한 뒤에야 움직이며, 지급은 승인 경로와 같은 멱등키를 쓴다.
그래서 버튼을 연타해도 중복 지급이 0건이다(완료 조건).

조회는 Viewer 도 한다. 결제사 대조도 조회다 — 바깥 호출이 붙지만 아무것도 바꾸지 않고,
운영자가 "우리 DB 가 틀렸는지"를 확인하는 것이 이 화면의 기본 동작이라 조회 권한에 둔다.
재처리만 Super Admin(`PAYMENT_WRITE`)이다.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_permission
from app.core.admin_rbac import PAYMENT_READ, PAYMENT_WRITE
from app.database import get_db
from app.models.admin import AdminUser
from app.schemas.admin_payment import (
    IssueFilter,
    OrderStatus,
    PaymentDetail,
    PaymentListResponse,
    ProviderPayment,
    RegrantRequest,
    RegrantResponse,
)
from app.services import admin_payments
from app.services.audit import record_admin_action
from app.services.rate_limit import client_ip

router = APIRouter()

_MENU = "결제"
_TARGET = "star_order"
_NOT_FOUND = "존재하지 않는 주문이에요."

_MAX_PAGE_SIZE = 100


async def _load(order_id: str, db: AsyncSession):
    row = await admin_payments.load_order(db, order_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return row


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    q: str | None = Query(default=None, max_length=64),
    order_status: OrderStatus | None = Query(default=None, alias="status"),
    issue: IssueFilter | None = Query(default=None),
    member_id: int | None = Query(default=None, ge=1),
    day_from: date | None = Query(default=None, alias="from"),
    day_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    _: AdminUser = Depends(require_permission(PAYMENT_READ)),
    db: AsyncSession = Depends(get_db),
):
    """결제 목록. 결제일(주문 생성일) 내림차순 고정, 날짜 필터는 KST 기준이다.

    유형별 이상 건수는 **필터와 무관하게 전체 기준**으로 함께 내려간다. 이상 목록을
    보려면 먼저 이상이 있다는 사실을 알아야 하는데, 필터를 건 뒤에야 세면 그 사실이
    화면에 영영 나타나지 않는다.
    """
    total, items = await admin_payments.list_orders(
        db,
        q=q,
        status=order_status,
        issue=issue,
        member_id=member_id,
        day_from=day_from,
        day_to=day_to,
        page=page,
        size=size,
    )
    return PaymentListResponse(
        total=total,
        page=page,
        size=size,
        items=items,
        issues=list(admin_payments.ISSUES),
        issue_counts=await admin_payments.issue_counts(
            db, now=admin_payments._utcnow()
        ),
    )


@router.get("/{order_id}", response_model=PaymentDetail)
async def get_payment(
    order_id: str,
    _: AdminUser = Depends(require_permission(PAYMENT_READ)),
    db: AsyncSession = Depends(get_db),
):
    order, ledger, user = await _load(order_id, db)
    return await admin_payments.build_detail(db, order, ledger, user)


@router.get("/{order_id}/provider", response_model=ProviderPayment)
async def get_provider_payment(
    order_id: str,
    _: AdminUser = Depends(require_permission(PAYMENT_READ)),
    db: AsyncSession = Depends(get_db),
):
    """결제사 대조 — 결제사에 직접 물어 그 답을 그대로 보여준다. 아무것도 바꾸지 않는다.

    상세 응답에 섞지 않고 따로 둔 이유는 두 가지다. 상세를 열 때마다 바깥 호출이 붙으면
    결제사 장애가 곧 화면 장애가 되고, 새로고침마다 호출이 늘어난다.
    """
    order, _ledger, _user = await _load(order_id, db)
    return await admin_payments.fetch_provider_payment(order)


@router.post("/{order_id}/regrant", response_model=RegrantResponse)
async def regrant_payment(
    order_id: str,
    data: RegrantRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission(PAYMENT_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """지급 재처리 (결제 성공 + 재화 미지급).

    **두 번째 클릭은 실패가 아니라 `granted=false` 다.** 멱등키가 막았다는 사실을 그대로
    알려 준다 — 오류로 돌려주면 운영자가 "안 됐나 보다" 하고 또 누른다.

    지급되지 않은 경우에도 감사 로그는 남긴다. 금전 가치가 걸린 버튼은 "눌렀다"는 사실
    자체가 기록 대상이다(B-2).
    """
    order, ledger, user = await _load(order_id, db)
    before_balance = user.star_balance
    granted, credited, message = await admin_payments.regrant(db, order, ledger, user)

    await record_admin_action(
        admin_id=admin.id,
        menu=_MENU,
        target_type=_TARGET,
        target_id=order.order_id,
        action="지급재처리",
        before={"user_id": user.id, "star_balance": before_balance, "credited": ledger is not None},
        after={"granted": granted, "credited_stars": credited, "star_balance": user.star_balance},
        reason=data.reason,
        ip=client_ip(request),
    )

    order, ledger, user = await _load(order_id, db)
    return RegrantResponse(
        granted=granted,
        credited_stars=credited,
        message=message,
        detail=await admin_payments.build_detail(db, order, ledger, user),
    )
