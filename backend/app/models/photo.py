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

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)