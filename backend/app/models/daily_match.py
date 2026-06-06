from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DailyMatch(Base):
    __tablename__ = "daily_matches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # 0..3 — see module docstring for slot semantics.
    slot_index = Column(Integer, nullable=False)
    # All 4 slots in a cycle share the exact same assigned_at, which lets
    # us trivially partition by cycle (`GROUP BY user_id, assigned_at`)
    # and recompute unlock times client-side.
    assigned_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

Index(
    "ix_daily_matches_user_assigned",
    DailyMatch.user_id,
    DailyMatch.assigned_at.desc(),
)