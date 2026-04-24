import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _validate_time(v: Optional[str]) -> Optional[str]:
    if v is not None and not _TIME_RE.match(v):
        raise ValueError("birth_time must be in HH:MM format (e.g. 14:30)")
    return v


class BirthDataCreate(BaseModel):
    """Full birth data input — used for POST (create or replace)."""

    birth_date: date
    birth_time: Optional[str] = None
    calendar_type: Literal["solar", "lunar"] = "solar"
    is_leap_month: bool = False
    gender: Literal["male", "female"]

    @field_validator("birth_time")
    @classmethod
    def check_birth_time(cls, v: Optional[str]) -> Optional[str]:
        return _validate_time(v)


class BirthDataUpdate(BaseModel):
    """Partial birth data input — used for PATCH (update only provided fields)."""

    birth_date: Optional[date] = None
    birth_time: Optional[str] = None
    calendar_type: Optional[Literal["solar", "lunar"]] = None
    is_leap_month: Optional[bool] = None
    gender: Optional[Literal["male", "female"]] = None

    @field_validator("birth_time")
    @classmethod
    def check_birth_time(cls, v: Optional[str]) -> Optional[str]:
        return _validate_time(v)


class ProfileUpdate(BaseModel):
    """Partial profile update — used for PATCH /users/me/profile."""

    nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    photo_url: Optional[str] = Field(default=None, max_length=512)


class UserProfileResponse(BaseModel):
    """User profile returned by the API."""

    id: int
    kakao_id: Optional[str] = None
    birth_date: Optional[date] = None
    birth_time: Optional[str] = None
    calendar_type: Optional[str] = None
    is_leap_month: bool
    gender: Optional[str] = None
    nickname: Optional[str] = None
    photo_url: Optional[str] = None
    is_paid: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
