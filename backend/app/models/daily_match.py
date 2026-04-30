"""Daily match assignments — 4-card slot system.

Each user gets a 4-card pack assigned every 48 hours. The 4 slots have
different unlock semantics:

  slot 0 → 사주 기반 무료. Unlocked at assigned_at.
  slot 1 → 자미두수 기반 유료. Unlocked at assigned_at, but photo blinded
           unless the viewer has paid.
  slot 2 → 사주 기반 무료. Unlocked at assigned_at + 24h.
  slot 3 → 자미두수 기반 유료. Unlocked at assigned_at + 24h, photo
           blinded unless paid.

A "cycle" = the 4 rows sharing one assigned_at. The next cycle is created
on the first /compatibility/today call after `assigned_at + 48h`. Older
rows stay in the table — `/compatibility/history` reads them all to build
the cumulative match history shown on /matching.
"""

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


# Speeds up the hot query: "give me the most recent cycle for user X".
Index(
    "ix_daily_matches_user_assigned",
    DailyMatch.user_id,
    DailyMatch.assigned_at.desc(),
)