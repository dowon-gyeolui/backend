"""매일 매칭 카드 배정 기록 — 4-슬롯 시스템.

한 cycle은 같은 assigned_at을 공유하는 4행으로 구성된다.
  slot 0: 사주 기반 무료 — assigned_at 부터 공개
  slot 1: 자미두수 기반 유료 — assigned_at 부터 공개, 결제 전 사진 블러
  slot 2: 사주 기반 무료 — assigned_at + 24h 부터 공개
  slot 3: 자미두수 기반 유료 — assigned_at + 24h 부터 공개 + 결제 필요

다음 cycle은 assigned_at + 48h 이후 첫 /compatibility/today 호출에서
생성된다. 기존 행은 남아 /compatibility/history 가 누적 매칭 히스토리
를 만드는 데 사용한다.
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