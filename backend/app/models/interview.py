"""연애 인터뷰 답변 모델(InterviewAnswer)."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InterviewAnswer(Base):
    __tablename__ = "interview_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "question_key", name="uq_interview_user_question"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    question_key = Column(String(40), nullable=False)
    answer = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
