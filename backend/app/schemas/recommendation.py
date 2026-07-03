"""오행 기반 개인/커플 추천 스키마."""

from typing import Optional

from pydantic import BaseModel


class RecommendationCard(BaseModel):
    user_id: int
    dominant_element: Optional[str] = None
    colors: list[str] = []
    places: list[str] = []
    styling: str = ""
    summary: str = ""


class PairRecommendation(BaseModel):
    user_a_id: int
    user_b_id: int
    compatibility_score: int
    strengths: list[str] = []
    cautions: list[str] = []
    conversation_starters: list[str] = []
    summary: Optional[str] = None
    sources: list[str] = []
