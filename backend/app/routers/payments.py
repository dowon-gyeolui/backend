"""스타 충전 주문 생성/승인 및 테스트 충전 엔드포인트."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.payment import (
    BalanceResponse,
    ConfirmRequest,
    OrderCreate,
    OrderResponse,
)
from app.services import payments as payments_service

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
    current_user: User = Depends(get_current_user),
):
    """토스 결제 성공 리다이렉트 후 최종 승인 + 스타 적립. 멱등."""
    await payments_service.confirm_payment(
        current_user, body.payment_key, body.order_id, body.amount, db
    )
    return BalanceResponse(star_balance=current_user.star_balance)


@router.post("/test-topup", response_model=BalanceResponse)
async def test_topup(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if settings.toss_secret_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    current_user.star_balance += _TEST_TOPUP_STARS
    await db.commit()
    return BalanceResponse(star_balance=current_user.star_balance)
