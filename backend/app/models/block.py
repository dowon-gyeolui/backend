"""사용자 차단 기록 — 채팅방 '나가기 + 상대 쪽도 삭제' 체크 시 적재.

한 행 = "blocker 가 blocked 를 차단했다". 기록은 단방향으로 남기지만
게이트(매칭 후보·페어 추천·채팅)는 양방향으로 검사해 서로 영구 제외한다.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, UniqueConstraint

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserBlock(Base):
    __tablename__ = "user_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_user_block_pair"),
    )

    id = Column(Integer, primary_key=True, index=True)
    blocker_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
