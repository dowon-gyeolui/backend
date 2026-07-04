"""사주/자미두수 요약·상세·오늘의 운세·행동 가이드 엔드포인트."""
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
from app.services.action_guide import build_action_guide, get_action_guide_ai
from app.services.cache import cache_get, cache_set
from app.services.fortune import compute_today_fortune, get_today_fortune_ai

router = APIRouter()

_LLM_CACHE_TTL_S = 7 * 24 * 3600


def _require_birth_date(user: User) -> None:
    if user.birth_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="생년월일을 먼저 입력해주세요. (POST /users/me/birth-data)",
        )


def _birth_fingerprint(user: User) -> str:
    return (
        f"{user.birth_date}:{user.birth_time}:{user.calendar_type}"
        f":{user.is_leap_month}:{user.gender}"
    )


@router.get("/me", response_model=SajuResponse)
async def get_my_saju(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Short saju summary used on the main /saju screen."""
    _require_birth_date(current_user)

    key = f"llm:saju:{current_user.id}:{_birth_fingerprint(current_user)}"
    cached = await cache_get(key)
    if cached:
        return SajuResponse.model_validate_json(cached)

    saju = saju_service.calculate(current_user)
    result = await saju_service.enrich_with_interpretation(saju, db)
    if result.interpretation_status == "ready":
        await cache_set(key, result.model_dump_json(), _LLM_CACHE_TTL_S)
    return result


@router.get("/me/detailed", response_model=DetailedSajuResponse)
async def get_my_saju_detailed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_birth_date(current_user)

    key = f"llm:saju-detailed:{current_user.id}:{_birth_fingerprint(current_user)}"
    cached = await cache_get(key)
    if cached:
        return DetailedSajuResponse.model_validate_json(cached)

    saju = saju_service.calculate(current_user)
    result = await saju_service.enrich_with_detailed_interpretation(saju, db)
    if (
        result.interpretation_status == "ready"
        and result.personality
        and result.love
        and result.wealth
        and result.advice
    ):
        await cache_set(key, result.model_dump_json(), _LLM_CACHE_TTL_S)
    return result


@router.get("/me/today-fortune", response_model=TodayFortuneResponse)
async def get_my_today_fortune(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_birth_date(current_user)
    fortune = await get_today_fortune_ai(current_user, db)
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
        badges=fortune.badges,
    )


@router.get("/me/action-guide", response_model=ActionGuideResponse)
async def get_my_action_guide(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_birth_date(current_user)
    guide = await get_action_guide_ai(current_user, db)
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

    key = f"llm:jamidusu:{current_user.id}:{_birth_fingerprint(current_user)}"
    cached = await cache_get(key)
    if cached:
        return JamidusuResponse.model_validate_json(cached)

    result = await saju_service.build_jamidusu_for(current_user)
    if result.interpretation_status == "ready":
        await cache_set(key, result.model_dump_json(), _LLM_CACHE_TTL_S)
    return result


@router.get("/me/jamidusu-deep", response_model=JamidusuDeepResponse)
async def get_my_jamidusu_deep(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_birth_date(current_user)

    key = f"llm:jamidusu-deep:{current_user.id}:{_birth_fingerprint(current_user)}"
    cached = await cache_get(key)
    if cached:
        return JamidusuDeepResponse.model_validate_json(cached)

    result = await saju_service.build_jamidusu_deep_for(current_user, db)
    if result.interpretation_status == "ready":
        await cache_set(key, result.model_dump_json(), _LLM_CACHE_TTL_S)
    return result