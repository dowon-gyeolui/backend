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


class DetailedSajuResponse(SajuResponse):
    """SajuResponse + 4-section LLM interpretation (성격/연애/재물/조언).

    Each section is a 2-3 sentence Korean interpretation grounded in the
    same RAG passages used for `interpretation`. Sections may be empty
    strings when the LLM failed for that category specifically; the
    frontend should render a graceful placeholder for empties.

    Health was intentionally removed — fortune-telling shouldn't make
    medical claims, and we don't want the user to act on them.
    """

    personality: str = ""
    love: str = ""
    wealth: str = ""
    advice: str = ""


class JamidusuPalace(BaseModel):
    """One of the 12 자미두수 palaces with its LLM-generated reading."""

    name: str         # e.g. "命宮 (명궁)"
    description: str  # one-line reading, 30~80자


class JamidusuResponse(BaseModel):
    """자미두수 (Zǐwēi Dòushù) interpretation for the premium drawer.

    Anchored on the user's saju (we don't compute a real 자미두수 chart for
    MVP — the LLM bridges between the two systems given saju context).

    `palaces` covers the canonical 12 궁; `main_stars_summary` describes
    where the major 14주성 cluster falls; `overview` is the closing
    paragraph the UI shows at the top.
    """

    user_id: int
    overview: str = ""
    palaces: list[JamidusuPalace] = []
    main_stars_summary: str = ""
    interpretation_status: Literal["pending", "ready"] = "pending"
