from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserPhotoResponse(BaseModel):
    """One row of the user_photos table."""

    id: int
    url: str
    is_primary: bool
    is_face_verified: bool = False
    position: int
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPhotoListResponse(BaseModel):
    """List of photos for /users/me/photos. Wrapped in an object so the
    response can grow (e.g. with quota / max counts) without breaking
    callers."""

    photos: list[UserPhotoResponse]
    primary_photo_url: Optional[str] = None