from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Frontend keeps these in sync — see components/matching/report-modal.tsx
ReportReason = Literal[
    "inappropriate",   # 부적절한 대화 (비속어, 욕설, 성희롱)
    "fake",            # 허위 정보 및 사칭 (사진 도용, 나이/성별 속임)
    "spam",            # 상업적 목적 및 스팸 (광고, 금전 요구)
    "other",           # 기타 (직접 입력)
]


class ReportCreate(BaseModel):
    reported_user_id: int
    reason: ReportReason
    # 기타 카테고리는 details 가 필수, 그 외는 선택. 검증은 라우터에서.
    details: Optional[str] = Field(default=None, max_length=1000)


class ReportResponse(BaseModel):
    id: int
    reporter_id: int
    reported_id: int
    reason: str
    details: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}