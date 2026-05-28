"""사용자 신고 기록 — 운명 분석 리포트 drawer 의 신고하기 흐름에서 적재.

Append-only로 기록해 운영팀이 추후 채팅 히스토리와 함께 검토할 수 있다.
details 컬럼은 "기타" 사유 카테고리에서 자유 서술을 받는 자리.
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