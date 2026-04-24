from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel


class BirthInputSummary(BaseModel):
    """Echoes the birth data that was used as calculation input."""

    birth_date: date
    birth_time: Optional[str] = None   # "HH:MM" or None if unknown
    calendar_type: str = "solar"       # "solar" | "lunar"
    is_leap_month: bool = False
    gender: Optional[str] = None


class Pillar(BaseModel):
    """One of the four saju pillars (년주/월주/일주/시주)."""

    label: str    # "년주" | "월주" | "일주" | "시주"
    stem: str     # 천간 (heavenly stem), e.g. "갑"
    branch: str   # 지지 (earthly branch), e.g. "자"
    combined: str  # stem + branch, e.g. "갑자"


class ElementProfile(BaseModel):
    """오행 (five elements) count derived from the four pillars' heavenly stems.

    TODO: Include earthly branches in real calculation for a full 8-character reading.
    """

    wood: int = 0   # 목(木)
    fire: int = 0   # 화(火)
    earth: int = 0  # 토(土)
    metal: int = 0  # 금(金)
    water: int = 0  # 수(水)


class SajuResponse(BaseModel):
    user_id: int
    input_summary: BirthInputSummary
    pillars: list[Pillar]          # [년주, 월주, 일주, 시주]
    element_profile: ElementProfile
    summary: str                   # Short Korean provisional summary

    # --- Retrieval-grounded interpretation layer ---
    # Pipeline:
    #   retrieved chunks (sources) → LLM summarization → interpretation
    #
    # interpretation_status semantics:
    #   "pending" — retrieval produced nothing relevant OR embedding unavailable
    #   "ready"   — retrieval returned at least one vector-similarity match
    #
    # `interpretation_sources` is the citation list (always populated when ready).
    # `interpretation` is the LLM-generated Korean summary, grounded strictly
    # in those sources. It may be null even when status="ready" if the LLM
    # call failed or was skipped — UI should gracefully fall back to showing
    # the citations alone.
    interpretation_status: Literal["pending", "ready"] = "pending"
    interpretation_sources: list[str] = []
    interpretation: Optional[str] = None
