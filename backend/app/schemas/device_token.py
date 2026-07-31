"""FCM 기기 토큰 등록 요청 스키마."""

from typing import Literal

from pydantic import BaseModel, Field


class DeviceTokenRegister(BaseModel):
    token: str = Field(min_length=1, max_length=255)
    platform: Literal["android", "ios"] = "android"
