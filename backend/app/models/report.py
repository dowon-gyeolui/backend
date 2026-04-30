"""User reports — 신고 기록.

Append-only. Stored so the moderation team can review chat history later.
The `details` column is free-form text used by the "기타" reason category.
"""

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
    # "기타" 카테고리에 들어가는 자유 서술. 다른 카테고리에서도 추가 설명을 담을 수 있음.
    details = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )