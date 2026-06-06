from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserPhotoResponse(BaseModel):
    id: int
    url: str
    is_primary: bool
    is_face_verified: bool = False
    position: int
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPhotoListResponse(BaseModel):
    photos: list[UserPhotoResponse]
    primary_photo_url: Optional[str] = None