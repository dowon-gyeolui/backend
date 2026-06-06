from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.saju import (
    ActionGuideResponse,
    DetailedSajuResponse,
    JamidusuDeepResponse,
    JamidusuResponse,
    SajuResponse,
    TodayFortuneResponse,
)
from app.services import saju as saju_service
from app.services.action_guide import build_action_guide
from app.services.fortune import compute_today_fortune

router = APIRouter()


def _require_birth_date(user: User) -> None:
    if user.birth_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="생년월일을 먼저 입력해주세요. (POST /users/me/birth-data)",
        )


@router.get("/me", response_model=SajuResponse)
async def get_my_saju(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Short saju summary used on the main /saju screen."""
    _require_birth_date(current_user)
    saju = saju_service.calculate(current_user)
    return await saju_service.enrich_with_interpretation(saju, db)


@router.get("/me/detailed", response_model=DetailedSajuResponse)
async def get_my_saju_detailed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """5-section deep interpretation: 성격 / 연애 / 재물 / 건강 / 조언.

    Same RAG passages as /me but a different LLM prompt that asks for
    one short paragraph per category. Cached lazily — call may take 5-10s
    on first request because of the OpenAI round-trip.
    """
    _require_birth_date(current_user)
    saju = saju_service.calculate(current_user)
    return await saju_service.enrich_with_detailed_interpretation(saju, db)


@router.get("/me/today-fortune", response_model=TodayFortuneResponse)
async def get_my_today_fortune(
    current_user: User = Depends(get_current_user),
):
    _require_birth_date(current_user)
    fortune = compute_today_fortune(current_user)
    if fortune is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="오늘의 인연운을 계산하지 못했어요. 사주 정보를 확인해주세요.",
        )
    return TodayFortuneResponse(
        fortune_text=fortune.fortune_text,
        today_pillar=fortune.today_pillar,
        today_pillar_hanja=fortune.today_pillar_hanja,
        relation=fortune.relation,
        element_today=fortune.element_today,
        score=fortune.score,
        headline=fortune.headline,
        person_type=fortune.person_type,
        timing=fortune.timing,
        place=fortune.place,
        caution=fortune.caution,
        lucky_color=fortune.lucky_color,
        badges=fortune.badges,
    )


@router.get("/me/action-guide", response_model=ActionGuideResponse)
async def get_my_action_guide(
    current_user: User = Depends(get_current_user),
):
    _require_birth_date(current_user)
    guide = build_action_guide(current_user)
    if guide is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="행동 가이드를 만들지 못했어요. 사주 정보를 확인해줘.",
        )
    return ActionGuideResponse(text=guide["text"])


@router.get("/me/jamidusu", response_model=JamidusuResponse)
async def get_my_jamidusu(
    current_user: User = Depends(get_current_user),
):
    _require_birth_date(current_user)
    return saju_service.build_jamidusu_for(current_user)


@router.get("/me/jamidusu-deep", response_model=JamidusuDeepResponse)
async def get_my_jamidusu_deep(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_birth_date(current_user)
    return await saju_service.build_jamidusu_deep_for(current_user, db)