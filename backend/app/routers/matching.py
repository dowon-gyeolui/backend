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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """오늘의 인연 1장(무료). 오늘 것이 없으면 새로 배정해 반환한다."""
    card = await matching_service.get_today_card(current_user, db)
    return TodayCardResponse(card=card)


@router.post("/unlock", response_model=UnlockResponse)
async def unlock_extra_card(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """추가 인연 유료 열람 — 별 10개 차감, 하루 10장 한도. 다음 후보를 공개."""
    card = await matching_service.unlock_extra(current_user, db)
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
