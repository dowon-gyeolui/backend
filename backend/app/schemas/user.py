import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_MBTI_RE = re.compile(r"^[EI][NS][TF][JP]$", re.IGNORECASE)


def _validate_time(v: Optional[str]) -> Optional[str]:
    if v is not None and not _TIME_RE.match(v):
        raise ValueError("birth_time must be in HH:MM format (e.g. 14:30)")
    return v


def _validate_mbti(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    if not _MBTI_RE.match(v):
        raise ValueError("mbti must be a 4-letter MBTI code (e.g. ENFP)")
    return v.upper()


class BirthDataCreate(BaseModel):
    """Full birth data input — used for POST (create or replace)."""

    birth_date: date
    birth_time: Optional[str] = None
    calendar_type: Literal["solar", "lunar"] = "solar"
    is_leap_month: bool = False
    gender: Literal["male", "female"]
    birth_place: Optional[str] = Field(default=None, max_length=50)

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
    birth_place: Optional[str] = Field(default=None, max_length=50)

    @field_validator("birth_time")
    @classmethod
    def check_birth_time(cls, v: Optional[str]) -> Optional[str]:
        return _validate_time(v)


class ProfileUpdate(BaseModel):
    """Partial profile update — used for PATCH /users/me/profile.

    Covers card-display fields (nickname/photo) plus the optional
    self-introduction (bio) and the structured "기본 정보" group used by the
    profile-completion gauge: height, MBTI, job, region, smoking, drinking,
    religion.
    """

    nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    photo_url: Optional[str] = Field(default=None, max_length=512)
    bio: Optional[str] = Field(default=None, max_length=120)

    height_cm: Optional[int] = Field(default=None, ge=100, le=250)
    mbti: Optional[str] = Field(default=None, max_length=4)
    job: Optional[str] = Field(default=None, max_length=50)
    region: Optional[str] = Field(default=None, max_length=50)
    # Figma uses X/O for smoking and a 4-step 음주 segmented control. The
    # legacy values ("안함"/"전자담배"/"흡연" and "안함"/"가끔"/"자주") still
    # validate so existing rows don't trip up the PATCH endpoint, but the
    # canonical values written by the new modal are the Figma ones.
    smoking: Optional[
        Literal["X", "O", "안함", "전자담배", "흡연"]
    ] = None
    drinking: Optional[
        Literal[
            "X", "1주에 1번", "1달에 1번", "자주 마심",
            "안함", "가끔", "자주",
        ]
    ] = None
    religion: Optional[
        Literal["무교", "기독교", "불교", "천주교", "기타"]
    ] = None

    @field_validator("mbti")
    @classmethod
    def check_mbti(cls, v: Optional[str]) -> Optional[str]:
        return _validate_mbti(v)


class UserProfileResponse(BaseModel):
    """User profile returned by the API."""

    id: int
    kakao_id: Optional[str] = None
    birth_date: Optional[date] = None
    birth_time: Optional[str] = None
    calendar_type: Optional[str] = None
    is_leap_month: bool
    gender: Optional[str] = None
    birth_place: Optional[str] = None
    nickname: Optional[str] = None
    photo_url: Optional[str] = None

    bio: Optional[str] = None

    # 기본 정보
    height_cm: Optional[int] = None
    mbti: Optional[str] = None
    job: Optional[str] = None
    region: Optional[str] = None
    smoking: Optional[str] = None
    drinking: Optional[str] = None
    religion: Optional[str] = None

    is_paid: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}