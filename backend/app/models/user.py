from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable until Kakao OAuth is wired up
    kakao_id = Column(String, unique=True, nullable=True, index=True)

    birth_date = Column(Date, nullable=True)
    birth_time = Column(String(5), nullable=True)      # "HH:MM"
    calendar_type = Column(String(10), nullable=True)  # "solar" | "lunar"
    is_leap_month = Column(Boolean, default=False, nullable=False)
    gender = Column(String(10), nullable=True)          # "male" | "female"

    is_paid = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
