"""스타 충전 결제 서비스 — 토스페이먼츠 단건결제 승인.

라우터(routers/payments.py)는 인증·검증만 책임지고 도메인 처리는 이 모듈로
위임한다. PRODUCT_CATALOG 가 상품↔금액↔스타 매핑의 단일 진실원이다 —
클라이언트가 보낸 금액은 저장된 주문 금액과 대조해 위변조를 차단한다.
"""

import base64
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.payment import (
    STATUS_FAILED,
    STATUS_PAID,
    STATUS_PENDING,
    StarOrder,
)
from app.models.user import User

TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"

# PRD 5번 BM 가격구조(VAT 포함). 상품ID → 금액(원) · 지급 스타.
PRODUCT_CATALOG: dict[str, dict] = {
    "STAR-001": {"price": 1100, "stars": 10, "name": "스타 10개"},
    "STAR-002": {"price": 5500, "stars": 50, "name": "스타 50개"},
    "STAR-003": {"price": 9900, "stars": 100, "name": "스타 100개"},
    "STAR-004": {"price": 19900, "stars": 220, "name": "스타 220개"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _gen_order_id() -> str:
    # 토스 orderId 규격: 6~64자 영숫자·-·_. uuid hex 로 충돌 없는 값 생성.
    import uuid

    return f"zami_{uuid.uuid4().hex}"


async def create_order(
    user: User, product_id: str, db: AsyncSession
) -> dict:
    product = PRODUCT_CATALOG.get(product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="존재하지 않는 상품입니다.",
        )

    order = StarOrder(
        user_id=user.id,
        order_id=_gen_order_id(),
        product_id=product_id,
        amount=product["price"],
        star_amount=product["stars"],
        status=STATUS_PENDING,
    )
    db.add(order)
    await db.commit()

    return {
        "order_id": order.order_id,
        "product_id": product_id,
        "amount": product["price"],
        "star_amount": product["stars"],
        "order_name": f"ZAMI {product['name']}",
    }


async def confirm_payment(
    user: User,
    payment_key: str,
    order_id: str,
    amount: int,
    db: AsyncSession,
) -> None:
    """토스 승인 호출 + 성공 시 스타 적립. 멱등(같은 주문 재호출 시 재적립 안 함)."""
    result = await db.execute(
        # Postgres 에서 동시 confirm 시 이중 적립을 막는 row lock.
        # SQLite 에서는 no-op.
        select(StarOrder).where(StarOrder.order_id == order_id).with_for_update()
    )
    order = result.scalar_one_or_none()

    if order is None or order.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="주문을 찾을 수 없습니다.",
        )

    # 이미 처리된 주문 → 재적립 없이 성공 반환(멱등).
    if order.status == STATUS_PAID:
        return

    if order.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 처리되었거나 유효하지 않은 주문입니다.",
        )

    # 위변조 차단 — 클라가 보낸 금액이 서버가 확정한 금액과 다르면 거부.
    if order.amount != amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="결제 금액이 주문 금액과 일치하지 않습니다.",
        )

    if not settings.toss_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="결제 설정이 완료되지 않았습니다.",
        )

    # 토스 인증: base64("{secretKey}:") — 비밀번호 자리는 비운다.
    encoded = base64.b64encode(f"{settings.toss_secret_key}:".encode()).decode()
    headers = {"Authorization": f"Basic {encoded}"}
    body = {"paymentKey": payment_key, "orderId": order_id, "amount": amount}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(TOSS_CONFIRM_URL, json=body, headers=headers)

    if resp.status_code != 200:
        order.status = STATUS_FAILED
        await db.commit()
        # 토스 에러 메시지(detail)를 그대로 노출하지 않고 일반화.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="결제 승인에 실패했습니다.",
        )

    order.status = STATUS_PAID
    order.payment_key = payment_key
    order.paid_at = _utcnow()
    user.star_balance += order.star_amount
    await db.commit()
