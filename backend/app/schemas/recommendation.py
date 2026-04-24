from typing import Optional

from pydantic import BaseModel


class RecommendationCard(BaseModel):
    """Pre-match recommendation — "좋은 인연을 만나기 위한 방법" (무료).

    Rule-based: derived from the user's saju dominant element.
    No LLM / no RAG — just deterministic mappings so it works without API keys.
    """

    user_id: int
    dominant_element: Optional[str] = None   # "목" | "화" | "토" | "금" | "수"
    colors: list[str] = []
    places: list[str] = []
    styling: str = ""
    summary: str = ""                        # Short Korean guidance


class PairRecommendation(BaseModel):
    """Post-match recommendation — "연인 확률을 높일 수 있는 방법" (유료).

    Derived per user pair. LLM produces `strengths` / `cautions` /
    `conversation_starters` grounded in retrieved classical passages.
    `sources` carries citation strings for UI evidence display.
    """

    user_a_id: int
    user_b_id: int
    compatibility_score: int
    strengths: list[str] = []
    cautions: list[str] = []
    conversation_starters: list[str] = []
    summary: Optional[str] = None            # LLM Korean 2~3 sentences
    sources: list[str] = []                  # source_citation strings
