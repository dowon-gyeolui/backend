"""사용자 신고 기록 모델(Report)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reported_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(String(40), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )