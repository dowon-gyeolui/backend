"""오늘의 인연 카드 조회/열람 응답 스키마."""

from typing import Optional

from pydantic import BaseModel

from app.schemas.compatibility import MatchCandidate


class TodayCardResponse(BaseModel):
    """오늘의 인연 — 후보 풀이 없으면 card=None (UI 가 '아직 인연 없음' 렌더).

    `relax_available` 는 **선호 조건 안에는 상대가 없지만 조건을 완화하면 있다**는 뜻이다.
    이때만 화면이 완화 동의 팝업(기다리기 / 조건 완화하기)을 띄운다(OI-MATCH-003).
    """

    card: Optional[MatchCandidate] = None
    relax_available: bool = False


class UnlockResponse(BaseModel):
    """추가 인연 유료 열람 결과."""

    card: MatchCandidate
    star_balance: int
    extra_unlocked_today: int
