"""FCM 기기 토큰 모델(DeviceToken) — 푸시 알림 발송 대상."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    platform = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
