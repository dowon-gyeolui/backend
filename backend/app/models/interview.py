"""연애 인터뷰 답변 — 온보딩 마지막 단계에서 사용자가 고른 질문에 답한 내용.

한 행 = "이 사용자가 이 질문(question_key)에 이렇게 답했다". 노출은 상호주의:
상대가 답한 개수만큼만 내 답을 볼 수 있다(공개 프로필 빌드에서 처리).

question_key 는 프론트 카탈로그(src/lib/interview.ts)의 안정적 식별자와 짝.
"""

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
