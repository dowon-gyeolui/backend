"""사주 궁합 점수/리포트 및 매칭 후보 스키마."""

from typing import Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    user_a_id: int
    user_b_id: int
    score: int
    summary: Optional[str] = None


class CompatibilityReport(BaseModel):
    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int

    summary_lines: list[str]

    keywords: list[str]


class MatchCandidate(BaseModel):
    user_id: int
    score: int
    nickname: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    is_blinded: bool = True

    photo_url: Optional[str] = None
    photos: list[str] = []
    bio: Optional[str] = None
    birth_year: Optional[int] = None
    dominant_element: Optional[str] = None
    mbti: Optional[str] = None

    is_face_verified: bool = False
