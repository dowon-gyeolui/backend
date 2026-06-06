from typing import Optional

from pydantic import BaseModel


class RecommendationCard(BaseModel):
    user_id: int
    dominant_element: Optional[str] = None   # "목" | "화" | "토" | "금" | "수"
    colors: list[str] = []
    places: list[str] = []
    styling: str = ""
    summary: str = ""                        # Short Korean guidance


class PairRecommendation(BaseModel):
    user_a_id: int
    user_b_id: int
    compatibility_score: int
    strengths: list[str] = []
    cautions: list[str] = []
    conversation_starters: list[str] = []
    summary: Optional[str] = None            # LLM Korean 2~3 sentences
    sources: list[str] = []                  # source_citation strings
