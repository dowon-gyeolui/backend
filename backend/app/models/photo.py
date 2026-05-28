"""다중 사진 프로필 갤러리.

users.photo_url 은 매칭 카드/채팅 헤더에서 쓰는 단일 메인 사진이지만,
사용자는 최대 6장까지 업로드하고 그중 하나를 메인으로 지정할 수 있다.
나머지를 별도 테이블에 분리해 두면 hot path(매칭 목록)는 가벼운 상태로
유지하면서 갤러리 엔드포인트에서만 한 번 더 조회해 펼치면 된다.

is_face_verified 플래그는 AWS Rekognition strict 모더레이션
(얼굴 1개 + 면적 25% 이상)을 통과한 사진에만 True 가 되며,
이 플래그가 켜진 사진에만 ZAMI 공식 인증 뱃지가 노출된다.
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