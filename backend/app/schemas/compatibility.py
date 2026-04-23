from typing import Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    user_a_id: int
    user_b_id: int
    score: int  # 0~100
    # Populated from retrieved knowledge chunks, not free-form LLM output
    # TODO: wire to knowledge retrieval service
    summary: Optional[str] = None


class MatchCandidate(BaseModel):
    user_id: int
    score: int
    is_blinded: bool = True  # Free tier: blinded profile
