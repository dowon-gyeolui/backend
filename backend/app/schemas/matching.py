"""오늘의 인연 카드 조회/열람 응답 스키마."""

from typing import Optional

from pydantic import BaseModel

from app.schemas.compatibility import MatchCandidate


class TodayCardResponse(BaseModel):
    """오늘의 인연 — 후보 풀이 없으면 card=None (UI 가 '아직 인연 없음' 렌더)."""

    card: Optional[MatchCandidate] = None


class UnlockResponse(BaseModel):
    """추가 인연 유료 열람 결과."""

    card: MatchCandidate
    star_balance: int
    extra_unlocked_today: int
