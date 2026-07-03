"""사용자 제재(스트라이크) 기록 모델(UserStrike)."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserStrike(Base):
    __tablename__ = "user_strikes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    detail = Column(String(120), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )