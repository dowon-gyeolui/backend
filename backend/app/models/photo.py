"""사용자 프로필 사진 갤러리 모델(UserPhoto)."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserPhoto(Base):
    __tablename__ = "user_photos"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    url = Column(String(512), nullable=False)
    public_id = Column(String(256), nullable=True)

    position = Column(Integer, default=0, nullable=False)

    is_primary = Column(Boolean, default=False, nullable=False)

    is_face_verified = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)