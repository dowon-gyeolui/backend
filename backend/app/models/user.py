"""사용자 테이블.

하나의 row 가 사주 입력값(생년월일/시간/양음력/성별/출생지),
프로필 카드 정보(닉네임/사진/한 줄 소개/키/MBTI/직업 등),
결제 상태(is_paid), 채팅 정지 쿨다운(chat_suspended_until)을 모두 담는다.

카카오 OAuth 가입 시 kakao_id 만 채워진 빈 row 가 먼저 생성되고,
온보딩 단계에서 생년월일/성별 등이 차례로 PATCH 된다.
"""

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

    # --- Saju inputs ---
    birth_date = Column(Date, nullable=True)
    birth_time = Column(String(5), nullable=True)      # "HH:MM"
    calendar_type = Column(String(10), nullable=True)  # "solar" | "lunar"
    is_leap_month = Column(Boolean, default=False, nullable=False)
    gender = Column(String(10), nullable=True)          # "male" | "female"
    # 출생지 — KST(135°E) 와 실제 경도 차이로 인한 시각 보정에 사용.
    # "서울특별시" 등 한국 17개 시·도 + "해외/기타" 가 들어올 수 있다.
    birth_place = Column(String(50), nullable=True)

    # --- Profile card fields (shown in match cards) ---
    nickname = Column(String(50), nullable=True)
    photo_url = Column(String(512), nullable=True)

    # --- 한 줄 자기소개 (counts toward 인연 탐색기 가동률) ---
    bio = Column(String(120), nullable=True)

    # --- 기본 정보 (counts toward 가동률 once any of height/mbti/job/region is set) ---
    height_cm = Column(Integer, nullable=True)
    mbti = Column(String(4), nullable=True)        # ENFP / INTJ / ...
    job = Column(String(50), nullable=True)
    region = Column(String(50), nullable=True)     # 서울 / 부산 ...
    smoking = Column(String(20), nullable=True)    # 안함 / 전자담배 / 흡연
    drinking = Column(String(20), nullable=True)   # 안함 / 가끔 / 자주
    religion = Column(String(20), nullable=True)   # 무교 / 기독교 / 불교 / 천주교 / 기타

    # --- Payment / flags ---
    is_paid = Column(Boolean, default=False, nullable=False)

    # Chat moderation cooldown — when set, attempts to send a chat
    # message before this timestamp are rejected with a 정지 message.
    # Cleared automatically by passing time (we just check `now <`).
    chat_suspended_until = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
