"""토스페이먼츠 스타 충전 주문 생성/승인 처리."""

import base64
from datetime import datetime, timedelta, timezone

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
from app.models.star_ledger import ENTRY_PURCHASE
from app.models.user import User
from app.services import star_ledger

TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"
# 주문번호로 결제를 되묻는다. 승인 요청이 끝내 오지 않은 주문을 정리할 때 쓴다.
TOSS_ORDER_QUERY_URL = "https://api.tosspayments.com/v1/payments/orders"

# 토스 결제 상태 중 돈이 실제로 들어온 상태. 여기서만 스타를 지급한다.
TOSS_STATUS_DONE = "DONE"
# 되살아날 수 없는 상태. 주문을 실패로 닫는다.
TOSS_DEAD_STATUSES = frozenset({"CANCELED", "PARTIAL_CANCELED", "ABORTED", "EXPIRED"})
# READY / IN_PROGRESS / WAITING_FOR_DEPOSIT 는 아직 진행 중이라 손대지 않는다.

# 토스에 결제 기록 자체가 없을 때 쓰는 내부 표식. 토스가 쓰지 않는 값이라 겹치지 않는다.
_NO_PAYMENT = "NO_PAYMENT"

# 결제 기록조차 없는 주문을 실패로 닫기까지 기다리는 시간.
# 사용자가 결제창에 도달하기 전에 앱이 잠깐 백그라운드로 갔을 뿐일 수 있다.
_ABANDON_AFTER = timedelta(minutes=10)

# 한 번의 정리에서 토스에 물어볼 주문 수 상한. 미확정 주문은 보통 0~1건이고,
# 앱이 켜질 때마다 부르는 경로라 바깥 호출이 무제한으로 늘지 않게 막는다.
_MAX_ORDERS_PER_RUN = 10

PRODUCT_CATALOG: dict[str, dict] = {
    "STAR-001": {"price": 1100, "stars": 10, "name": "스타 10개"},
    "STAR-002": {"price": 5500, "stars": 50, "name": "스타 50개"},
    "STAR-003": {"price": 9900, "stars": 100, "name": "스타 100개"},
    "STAR-004": {"price": 19900, "stars": 220, "name": "스타 220개"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _gen_order_id() -> str:
    import uuid

    return f"melobe_{uuid.uuid4().hex}"


def _toss_headers() -> dict[str, str]:
    """토스 API 의 Basic 인증 헤더. 시크릿 키를 아이디로, 비밀번호는 빈 값."""
    encoded = base64.b64encode(f"{settings.toss_secret_key}:".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


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
        "order_name": f"MeloBe {product['name']}",
    }


async def confirm_payment(
    user: User,
    payment_key: str,
    order_id: str,
    amount: int,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(StarOrder).where(StarOrder.order_id == order_id).with_for_update()
    )
    order = result.scalar_one_or_none()

    if order is None or order.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="주문을 찾을 수 없습니다.",
        )

    if order.status == STATUS_PAID:
        return

    if order.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 처리되었거나 유효하지 않은 주문입니다.",
        )

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

    body = {"paymentKey": payment_key, "orderId": order_id, "amount": amount}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(TOSS_CONFIRM_URL, json=body, headers=_toss_headers())

    if resp.status_code != 200:
        order.status = STATUS_FAILED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="결제 승인에 실패했습니다.",
        )

    order.status = STATUS_PAID
    order.payment_key = payment_key
    order.paid_at = _utcnow()
    # 위의 상태 검사만으로도 재승인은 막히지만, 원장의 멱등키(주문번호+지급유형)가
    # 동시에 들어온 두 승인 요청까지 막는 마지막 방어선이다.
    await star_ledger.record(
        db,
        user,
        entry_type=ENTRY_PURCHASE,
        reference_id=order.order_id,
        amount=order.star_amount,
    )
    await db.commit()


async def _query_toss_order(client: httpx.AsyncClient, order_id: str) -> dict | None:
    """주문번호로 토스에 결제를 묻는다.

    - `dict`  = 토스의 답(결제 기록이 없으면 `{"status": _NO_PAYMENT}`)
    - `None`  = 물어보지 못했다(네트워크·서버 오류). 주문은 손대지 않고 다음 기회에 다시 본다.
    """
    try:
        resp = await client.get(
            f"{TOSS_ORDER_QUERY_URL}/{order_id}", headers=_toss_headers()
        )
    except httpx.HTTPError:
        return None

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        # NOT_FOUND_PAYMENT — 주문은 우리가 만들었지만 사용자가 결제창까지 가지 못했다.
        return {"status": _NO_PAYMENT}
    return None


async def reconcile_pending_orders(user: User, db: AsyncSession) -> dict:
    """미확정 주문을 결제사 상태 기준으로 정리한다. (OI-PAY-001: 결제사가 원천)

    결제 도중 앱이 죽거나 사용자가 브라우저를 닫으면 승인 요청(`confirm_payment`)이
    영영 오지 않아 주문이 PENDING 으로 남는다. 앱이 다시 켜질 때 이 함수를 불러
    **토스에 직접 물어본 결과대로** 성공이면 지급하고 실패면 주문을 닫는다.

    지급은 승인 경로와 같은 멱등키(주문번호 + `purchase`)를 쓰므로, 정리와 승인이
    겹쳐 들어와도 스타는 한 번만 들어간다.
    """
    result = await db.execute(
        select(StarOrder)
        .where(StarOrder.user_id == user.id, StarOrder.status == STATUS_PENDING)
        .order_by(StarOrder.created_at.desc())
        .limit(_MAX_ORDERS_PER_RUN)
    )
    pending = list(result.scalars().all())

    # 키가 없으면 물어볼 곳이 없다. 주문은 그대로 두고 조용히 지나간다 —
    # 앱이 켜질 때마다 부르는 경로라 설정 문제로 화면을 막으면 안 된다.
    if not pending or not settings.toss_secret_key:
        return {
            "star_balance": user.star_balance,
            "credited_stars": 0,
            "settled_orders": 0,
        }

    credited_stars = 0
    settled_orders = 0

    # 주문 행을 잠그지 않는다. 토스 호출이 끼어 있어 락을 오래 붙들게 되고,
    # 이중 지급은 원장의 멱등키가 이미 막고 있다.
    async with httpx.AsyncClient(timeout=10.0) as client:
        for order in pending:
            payment = await _query_toss_order(client, order.order_id)
            if payment is None:
                continue
            toss_status = payment.get("status")

            if toss_status == TOSS_STATUS_DONE:
                if payment.get("totalAmount") != order.amount:
                    # 결제된 금액이 주문 금액과 다르다. 자동 지급하지 않고 미확정으로
                    # 남겨 사람이 보게 한다(관리자 결제 화면의 지급 이상 필터 대상).
                    continue
                order.status = STATUS_PAID
                order.payment_key = payment.get("paymentKey")
                order.paid_at = _utcnow()
                granted = await star_ledger.record(
                    db,
                    user,
                    entry_type=ENTRY_PURCHASE,
                    reference_id=order.order_id,
                    amount=order.star_amount,
                )
                if granted:
                    credited_stars += order.star_amount
                settled_orders += 1
            elif toss_status in TOSS_DEAD_STATUSES:
                order.status = STATUS_FAILED
                settled_orders += 1
            elif toss_status == _NO_PAYMENT and _is_abandoned(order):
                order.status = STATUS_FAILED
                settled_orders += 1

    await db.commit()
    return {
        "star_balance": user.star_balance,
        "credited_stars": credited_stars,
        "settled_orders": settled_orders,
    }


def _is_abandoned(order: StarOrder) -> bool:
    """결제 기록 없는 주문을 닫아도 될 만큼 오래됐는지."""
    created = order.created_at
    if created.tzinfo is None:  # SQLite 는 tz 를 떨어뜨려 돌려준다. 저장은 항상 UTC.
        created = created.replace(tzinfo=timezone.utc)
    return _utcnow() - created >= _ABANDON_AFTER
