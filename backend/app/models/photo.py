"""User photos — multiple-photo profile gallery.

Why a separate table: users.photo_url is the single "main" image used by
match cards and chat headers, but the user can upload multiple photos and
pick which one is primary. Storing extras in a child table keeps the hot
path (match list) lightweight while still letting the gallery endpoint
hydrate everything in one extra query.
"""

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

    # Cloudinary delivery URL (.jpg). Same shape as users.photo_url.
    url = Column(String(512), nullable=False)
    # Cloudinary public_id — needed so DELETE /users/me/photos/{id} can also
    # remove the asset from Cloudinary instead of leaving orphans.
    public_id = Column(String(256), nullable=True)

    # Display order. 0 = first thumbnail. Primary photo is selected via
    # `is_primary`, not by position — order is purely cosmetic.
    position = Column(Integer, default=0, nullable=False)

    # Exactly one row per user should have is_primary=True. Enforced in
    # the service layer (set_primary swaps the flag atomically).
    is_primary = Column(Boolean, default=False, nullable=False)

    # AWS Rekognition 의 strict face check (얼굴 1개 + 면적 25% 이상 등)
    # 통과 여부. 새 업로드는 항상 True (option B 정책상 통과 못 하면
    # 업로드 자체가 거절). 컬럼 추가 시점에 이미 있던 행은 default
    # False — 기존 업로드 시점엔 8% 기준이었으므로 strict 통과 보장 못 함.
    # 이 플래그가 True 인 사진에만 ZAMI 공식 인증 뱃지가 노출된다.
    is_face_verified = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)