"""일별 AI 생성 텍스트 캐시 모델(DailyAiText) — 오늘의 운세/행동 가이드."""

from datetime import datetime, timezone

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint)

from app.database import Base

KIND_FORTUNE = "fortune"
KIND_ACTION_GUIDE = "action_guide"

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class DailyAiText(Base):
    __tablename__ = "daily_ai_texts"
    __table_args__ = (
        UniqueConstraint("user_id", "kst_date", "kind", name = "uq_daily_ai_text"),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kst_date = Column(Date, nullable=False)
    kind = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)