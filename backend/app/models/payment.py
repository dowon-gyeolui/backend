"""스타 충전 주문 모델(StarOrder) — 토스페이먼츠 단건결제 기록."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base

STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_FAILED = "FAILED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StarOrder(Base):
    __tablename__ = "star_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(String(64), nullable=False, unique=True, index=True)
    product_id = Column(String(20), nullable=False)
    amount = Column(Integer, nullable=False)
    star_amount = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_PENDING)
    payment_key = Column(String(200), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
