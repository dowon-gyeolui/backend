"""오늘의 인연 카드 조회/언락 및 열람 목록 엔드포인트."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.compatibility import MatchCandidate
from app.schemas.matching import TodayCardResponse, UnlockResponse
from app.services import matching as matching_service

router = APIRouter()


@router.get("/today", response_model=TodayCardResponse)
async def get_today_card(
    relax: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """오늘의 인연 1장(무료). 오늘 것이 없으면 새로 배정해 반환한다.

    `relax=true` 는 **사용자가 완화 동의 팝업에서 '조건 완화하기'를 누른 경우에만** 붙는다.
    붙지 않으면 선호 조건을 벗어난 카드를 만들지 않는다(OI-MATCH-003).
    """
    card, relax_available = await matching_service.get_today_card(
        current_user, db, relax=relax
    )
    return TodayCardResponse(card=card, relax_available=relax_available)


@router.post("/unlock", response_model=UnlockResponse)
async def unlock_extra_card(
    relax: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """추가 인연 유료 열람 — 별 10개 차감, 하루 10장 한도. 다음 후보를 공개.

    선호 조건 안에 상대가 없으면 별을 쓰지 않고 409 로 완화 동의를 요구한다.
    `relax=true` 로 다시 부르면 그때 완화 후보를 배정한다(OI-MATCH-003).
    """
    card = await matching_service.unlock_extra(current_user, db, relax=relax)
    return UnlockResponse(
        card=card,
        star_balance=current_user.star_balance,
        extra_unlocked_today=await matching_service.count_extra_today(
            current_user.id, db
        ),
    )


@router.get("", response_model=list[MatchCandidate])
async def list_unlocked_cards(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """열람한 카드 목록(최근순) — 채팅 가능한 상대들."""
    return await matching_service.list_unlocked(current_user, db)
