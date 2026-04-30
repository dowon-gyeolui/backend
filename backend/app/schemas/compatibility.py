from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    """Pairwise compatibility score between two users."""

    user_a_id: int
    user_b_id: int
    score: int  # 0~100
    # Short Korean summary built from pillar comparison + element balance.
    summary: Optional[str] = None


class DestinyAnalysis(BaseModel):
    """운명의 실타래 — 두 사람 사주의 심층 비교 (5 섹션)."""

    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int
    intro: str = ""
    personality: str = ""
    love_style: str = ""
    caution: str = ""
    longterm: str = ""
    interpretation_status: str = "pending"  # "pending" | "ready"


class DateSpot(BaseModel):
    title: str
    description: str


class DateRecommendation(BaseModel):
    """LLM-generated date spot suggestions for a paid pair."""

    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int
    overview: str = ""
    spots: list[DateSpot] = []
    interpretation_status: str = "pending"  # "pending" | "ready"


class CompatibilityReport(BaseModel):
    """Drawer-style 운명 분석 리포트 for the chat header.

    Two narrative bullets (synergy + caution) + 3 hashtag keywords feed the
    Figma drawer at node 37:1657. CTA gating is a frontend concern.
    """

    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int  # 0~100

    # First line is the synergy/strength; second is the caution/risk. Two
    # bullets line up with Figma's two ✦-prefixed paragraphs.
    summary_lines: list[str]

    # Three hashtag-style chips (e.g. "#금의_기운", "#찰떡궁합", "#솔직한_대화")
    keywords: list[str]


class MatchCandidate(BaseModel):
    """One match candidate shown as a profile card.

    Fields always visible:
      user_id, score, nickname, age, gender, is_blinded

    Free tier (is_blinded=True):
      photo_url = None            — 사진 블라인드(모자이크 대체)
      birth_year = None           — 정확한 연도 비공개
      dominant_element = None

    Paid tier (is_blinded=False):
      photo_url                   — 본 사진 공개
      birth_year                  — 생년 노출
      dominant_element            — 주요 오행 공개로 대화 맥락 제공
    """

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


class DailyMatchSlot(BaseModel):
    """One slot within today's 4-card pack.

    Slot policy (mirrored client-side):
      0 → 사주 무료, 즉시 unlock.
      1 → 자미두수 유료, 즉시 unlock 하지만 무료 사용자는 사진 블라인드.
      2 → 사주 무료, assigned_at + 24h 후 unlock.
      3 → 자미두수 유료, assigned_at + 24h 후 unlock + 무료 블라인드.

    `is_locked` 는 24h 카운트다운 잠금. `is_blinded` 는 결제 잠금. 둘은
    독립이며 클라이언트에서 잠금/블러를 따로 표현한다.
    """

    slot_index: int
    match_basis: Literal["saju", "jamidusu"]
    candidate: MatchCandidate
    assigned_at: datetime
    unlock_at: datetime
    is_locked: bool          # 카운트다운 잠금 (24h 미경과)
    requires_payment: bool   # 슬롯 자체가 유료 (slot 1,3)


class DailyMatchPack(BaseModel):
    """Today's 4-card pack response."""

    assigned_at: datetime
    next_cycle_at: datetime  # assigned_at + 48h
    slots: list[DailyMatchSlot]  # length always 4 (0..3)


class HistoryMatchEntry(BaseModel):
    """One row in the cumulative match history list."""

    candidate: MatchCandidate
    slot_index: int
    match_basis: Literal["saju", "jamidusu"]
    assigned_at: datetime
    unlock_at: datetime
    is_locked: bool
    requires_payment: bool
