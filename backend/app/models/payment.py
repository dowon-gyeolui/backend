"""스타 충전 주문 — 토스페이먼츠 단건결제 기록.

결제 흐름은 2단계다:
  1. create_order: 서버가 orderId·금액·지급 스타 수를 확정해 PENDING 으로 적재
  2. confirm: 토스 승인 API 성공 시 PAID 로 전환 + user.star_balance 적립

금액·상품↔스타 매핑은 전적으로 서버 카탈로그(services/payments.PRODUCT_CATALOG)
가 결정한다. 클라이언트가 보낸 금액을 신뢰하지 않는다(위변조 차단).
payment_key 에 unique 제약을 걸어 같은 결제가 두 번 적립되지 않게 막는다.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base

# 주문 상태값.
STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_FAILED = "FAILED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StarOrder(Base):
    __tablename__ = "star_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 토스 결제창에 넘기는 주문번호. 우리가 발급한다.
    order_id = Column(String(64), nullable=False, unique=True, index=True)
    product_id = Column(String(20), nullable=False)  # "STAR-002" 등
    amount = Column(Integer, nullable=False)  # 결제 금액(원)
    star_amount = Column(Integer, nullable=False)  # 지급할 스타 수
    status = Column(String(20), nullable=False, default=STATUS_PENDING)
    # 토스 승인 후 채워짐. unique 로 중복 적립 차단.
    payment_key = Column(String(200), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
