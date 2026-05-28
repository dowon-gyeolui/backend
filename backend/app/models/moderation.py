"""사용자 모더레이션 스트라이크 + 채팅 정지 쿨다운 기록.

채팅 메시지가 chat_moderation에 의해 자동 차단될 때마다
UserStrike 한 행을 append 한다. 누적 카운트가 임계치를 넘으면
24h 채팅 정지(users.chat_suspended_until)를 걸어 수동 운영 없이도
반복 위반자를 일시 차단한다.

사진 모더레이션(photo_moderation)에서 거절된 업로드는 스트라이크를
쌓지 않는다 — 사용자에게 거절 사유만 보여주고 재업로드를 허용한다.
채팅에서만 누적 추적하는 이유는 그곳이 실제 왕복 학대가 발생하는
지점이기 때문.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserStrike(Base):
    """Append-only audit log of a single moderation block.

    `kind` matches the ChatModerationResult.kind values:
    contact_leak | profanity | harassment | sexual | spam | other.
    """

    __tablename__ = "user_strikes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    # Free-form short detail for moderator triage (e.g. "phone", "openai_cat=harassment").
    detail = Column(String(120), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )