"""사용자 프로필 모델(User)."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Integer,
    String,
)

from app.database import Base

# 회원 상태(OI-MEM-001 확정: 정상 / 비활성 / 차단 / 탈퇴).
# **탈퇴는 여기에 없다.** 탈퇴는 행을 지우는 hard delete 이고(PIPA, services/users.delete_account)
# 복구하지 않는 것이 확정이라, 상태값으로 표현하면 "탈퇴 상태로 남아 있는 회원"이라는
# 존재하지 않는 상태가 생긴다. 관리자 화면은 "행이 없음"을 탈퇴로 읽는다.
STATUS_ACTIVE = "active"      # 정상
STATUS_INACTIVE = "inactive"  # 비활성 — 운영이 잠시 내린 상태
STATUS_BLOCKED = "blocked"    # 차단 — 제재로 막은 상태
STATUSES = (STATUS_ACTIVE, STATUS_INACTIVE, STATUS_BLOCKED)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # 잔액은 원장(StarLedger)을 통해서만 바뀌어야 하고, 어떤 경로로도 음수가 될 수
        # 없다. 앱 레이어 검사를 빠뜨린 코드가 생겨도 여기서 마지막으로 막힌다.
        CheckConstraint("star_balance >= 0", name="ck_users_star_balance_non_negative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    kakao_id = Column(String, unique=True, nullable=True, index=True)

    username = Column(String(20), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)

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

    pref_age_min = Column(Integer, nullable=True)
    pref_age_max = Column(Integer, nullable=True)
    pref_region = Column(String(50), nullable=True)
    pref_height_min = Column(Integer, nullable=True)

    # 상태와 노출 여부는 따로 둔다. "정상이지만 프로필만 내린 회원"(사진 신고 검수 중 등)이
    # 실제로 있고, 상태 하나로 합치면 그 회원을 비활성으로 만들어 로그인까지 막게 된다.
    status = Column(String(20), default=STATUS_ACTIVE, nullable=False)
    profile_hidden = Column(Boolean, default=False, nullable=False)

    is_paid = Column(Boolean, default=False, nullable=False)
    star_balance = Column(Integer, default = 0, nullable = False)
    chat_suspended_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
