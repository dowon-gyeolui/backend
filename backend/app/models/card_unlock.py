"""인연 카드 열람 기록 모델(CardUnlock)."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.database import Base

KIND_DAILY = "daily"
KIND_EXTRA = "extra"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CardUnlock(Base):
    __tablename__ = "card_unlocks"
    __table_args__ = (
        UniqueConstraint("user_id", "candidate_id", name="uq_card_unlock_pair"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(10), nullable=False)
    unlocked_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
