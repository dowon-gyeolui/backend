"""사용자 신고 생성/응답 스키마."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ReportReason = Literal[
    "inappropriate",
    "fake",
    "spam",
    "other",
]

class ReportCreate(BaseModel):
    reported_user_id: int
    reason: ReportReason
    details: Optional[str] = Field(default=None, max_length=1000)


class ReportResponse(BaseModel):
    id: int
    reporter_id: int
    reported_id: int
    reason: str
    details: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}