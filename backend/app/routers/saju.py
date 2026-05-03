from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.saju import (
    DetailedSajuResponse,
    JamidusuResponse,
    SajuResponse,
    TodayFortuneResponse,
)
from app.services import saju as saju_service
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
    """오늘의 인연운 — 사용자 사주 + 오늘 일진(日辰) 기반 일일 fortune.

    매일 KST 자정에 일주가 바뀌므로 결과 문구도 매일 갱신됨. 같은 날
    동일 사용자는 항상 동일 문구 (date+user_id seed). LLM 호출 없이
    rule-based 템플릿 풀에서 결정론적 선택.
    """
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
    )


@router.get("/me/jamidusu", response_model=JamidusuResponse)
async def get_my_jamidusu(
    current_user: User = Depends(get_current_user),
):
    """자미두수 12궁·14주성 LLM 풀이 — 프리미엄 사용자 전용 페이지에서 호출.

    LLM 라운드트립이 들어가서 5~10초 정도 걸릴 수 있습니다. 실패 시
    interpretation_status='pending' 으로 반환되며 클라이언트는 placeholder
    문구로 폴백합니다.
    """
    _require_birth_date(current_user)
    return saju_service.build_jamidusu_for(current_user)