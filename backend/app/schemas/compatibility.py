from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    user_a_id: int
    user_b_id: int
    score: int  # 0~100
    # Short Korean summary built from pillar comparison + element balance.
    summary: Optional[str] = None


class CompatibilityReport(BaseModel):
    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int  # 0~100

    summary_lines: list[str]

    keywords: list[str]


class MatchCandidate(BaseModel):
    user_id: int
    score: int  # 0~100
    nickname: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    is_blinded: bool = True

    # Unblinded-only extras (null when is_blinded=True)
    photo_url: Optional[str] = None
    birth_year: Optional[int] = None
    dominant_element: Optional[str] = None
    mbti: Optional[str] = None

    is_face_verified: bool = False


class DailyMatchSlot(BaseModel):
    slot_index: int
    match_basis: Literal["saju", "jamidusu"]
    candidate: MatchCandidate
    assigned_at: datetime
    unlock_at: datetime
    is_locked: bool          # 카운트다운 잠금 (24h 미경과)
    requires_payment: bool   # 슬롯 자체가 유료 (slot 1,3)


class DailyMatchPack(BaseModel):
    assigned_at: datetime
    next_cycle_at: datetime  # assigned_at + 48h
    slots: list[DailyMatchSlot]  # length always 4 (0..3)


class HistoryMatchEntry(BaseModel):
    candidate: MatchCandidate
    slot_index: int
    match_basis: Literal["saju", "jamidusu"]
    assigned_at: datetime
    unlock_at: datetime
    is_locked: bool
    requires_payment: bool
