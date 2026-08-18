"""스타 충전 주문 생성/승인 및 테스트 충전 엔드포인트."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_user, require_recent_auth
from app.database import get_db
from app.models.star_ledger import ENTRY_TEST_TOPUP
from app.models.user import User
from app.schemas.payment import (
    BalanceResponse,
    ConfirmRequest,
    OrderCreate,
    OrderResponse,
    ReconcileResponse,
)
from app.services import payments as payments_service
from app.services import star_ledger

router = APIRouter()

_TEST_TOPUP_STARS = 100


@router.post("/orders", response_model=OrderResponse)
async def create_order(
    body: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """스타 충전 주문 생성. 서버가 금액·지급 스타를 확정해 반환한다.

    프론트는 이 응답으로 토스 결제창을 호출한다.
    """
    return await payments_service.create_order(current_user, body.product_id, db)


@router.post("/confirm", response_model=BalanceResponse)
async def confirm_payment(
    body: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_recent_auth),
):
    """토스 결제 성공 리다이렉트 후 최종 승인 + 스타 적립. 멱등.

    돈이 나가는 지점이라 최근 인증을 요구한다(OI-AUTH-002). 재인증 없이 막히면 주문은
    PENDING 으로 남고 토스에서도 승인되지 않는다 — `/payments/reconcile` 이 나중에
    실패로 닫으므로 결제만 안 될 뿐 돈이 붕 뜨지는 않는다.
    """
    await payments_service.confirm_payment(
        current_user, body.payment_key, body.order_id, body.amount, db
    )
    return BalanceResponse(star_balance=current_user.star_balance)


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_pending(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """미확정 주문을 결제사 상태 기준으로 정리한다. 앱이 다시 켜질 때 호출한다.

    결제 도중 앱이 죽으면 승인 요청이 서버에 닿지 않아 주문이 붕 뜬다.
    성공한 결제였으면 스타를 지급하고, 실패·이탈이었으면 주문을 닫는다. 멱등하다.
    """
    result = await payments_service.reconcile_pending_orders(current_user, db)
    return ReconcileResponse(**result)


@router.post("/test-topup", response_model=BalanceResponse)
async def test_topup(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 무료로 스타를 얹어주는 테스트 경로다. 운영(debug=False)에서는 존재 자체를 숨긴다.
    # 결제 키가 붙어 있으면 개발 환경이라도 실결제와 섞이지 않게 함께 막는다.
    if not settings.debug or settings.toss_secret_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    # 개발용이라도 잔액에 직접 대입하지 않는다 — "잔액은 원장으로만 바뀐다"가 깨지면
    # 원장 합계와 잔액이 어긋나 대사(對査)가 불가능해진다. 매 호출이 새 충전이므로
    # 멱등키는 매번 새로 만든다.
    await star_ledger.record(
        db,
        current_user,
        entry_type=ENTRY_TEST_TOPUP,
        reference_id=uuid.uuid4().hex,
        amount=_TEST_TOPUP_STARS,
    )
    await db.commit()
    return BalanceResponse(star_balance=current_user.star_balance)
