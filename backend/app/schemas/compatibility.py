from typing import Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    """Pairwise compatibility score between two users."""

    user_a_id: int
    user_b_id: int
    score: int  # 0~100
    # Short Korean summary built from pillar comparison + element balance.
    # TODO: Optionally attach retrieval-grounded interpretation sources later.
    summary: Optional[str] = None


class MatchCandidate(BaseModel):
    """One match candidate in the /compatibility/matches list.

    Blinded profile (free tier): only user_id, score, gender, is_blinded=True.
    Unblinded profile (paid tier): also reveals the candidate's birth_year so
    the caller has enough saju context to decide whether to pursue the match.
    """

    user_id: int
    score: int  # 0~100
    gender: Optional[str] = None
    is_blinded: bool = True
    # Unblinded-only fields (None when is_blinded=True)
    birth_year: Optional[int] = None
    dominant_element: Optional[str] = None  # "목" | "화" | "토" | "금" | "수"
