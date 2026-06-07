from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    kakao_id = Column(String, unique=True, nullable=True, index=True)

    birth_date = Column(Date, nullable=True)
    birth_time = Column(String(5), nullable=True)      
    calendar_type = Column(String(10), nullable=True)  
    is_leap_month = Column(Boolean, default=False, nullable=False)
    gender = Column(String(10), nullable=True)
    birth_place = Column(String(50), nullable=True)

    nickname = Column(String(50), nullable=True)
    photo_url = Column(String(512), nullable=True)

    bio = Column(String(120), nullable=True)

    height_cm = Column(Integer, nullable=True)
    mbti = Column(String(4), nullable=True)        
    job = Column(String(50), nullable=True)
    region = Column(String(50), nullable=True)     
    smoking = Column(String(20), nullable=True)    
    drinking = Column(String(20), nullable=True)   
    religion = Column(String(20), nullable=True)
    is_paid = Column(Boolean, default=False, nullable=False)
    star_balance = Column(Integer, default = 0, nullable = False)
    chat_suspended_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
