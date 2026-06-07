"""인연 카드 열람 기록 — PRD 카드 모델의 핵심 상태.

한 행 = "이 사용자가 이 상대 카드를 열람했다". 용도:
  - 동일 사용자 재추천 불가: 후보 풀에서 이미 열람한 candidate 를 제외
  - 채팅 게이트: 열람한 상대와만 채팅 가능 (chat 라우터가 참조)
  - 하루 추가 열람 한도(10장): kind='extra' 행을 KST 일 단위로 카운트

kind:
  - 'daily' : 오늘의 인연(무료 1장/일)
  - 'extra' : 추가 인연(별 10개 차감, 하루 10장)
"""

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
        # 같은 상대를 두 번 열람/과금하지 않는다(재추천 불가와 동일 보장).
        UniqueConstraint("user_id", "candidate_id", name="uq_card_unlock_pair"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(10), nullable=False)  # 'daily' | 'extra'
    unlocked_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
