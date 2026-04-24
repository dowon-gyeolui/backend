from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.recommendation import PairRecommendation, RecommendationCard
from app.services import recommendations as rec_service

router = APIRouter()


@router.get(
    "/me",
    response_model=RecommendationCard,
    summary="사전 추천 — 좋은 인연을 만나기 위한 컬러/장소/스타일 (무료)",
)
async def get_my_recommendation(
    current_user: User = Depends(get_current_user),
):
    return rec_service.recommend_pre_match(current_user)


@router.get(
    "/pair/{target_user_id}",
    response_model=PairRecommendation,
    summary="사후 추천 — 매칭된 상대와의 대화/데이트 팁 (유료)",
    description=(
        "매칭된 두 사용자의 궁합·오행 관계를 바탕으로 "
        "원전 구절에서 관련 구절을 검색하고, LLM이 강점/유의점/대화 주제를 "
        "한국어로 생성합니다. 유료 유저에게만 공개됩니다."
    ),
)
async def get_pair_recommendation(
    target_user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_paid:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="유료 기능입니다. 상세 추천은 결제 후 이용하실 수 있습니다.",
        )
    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신과의 추천은 제공하지 않습니다.",
        )
    if current_user.birth_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="본인의 생년월일을 먼저 입력해주세요.",
        )

    target = await db.get(User, target_user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={target_user_id} 를 찾을 수 없습니다.",
        )
    if target.birth_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="상대의 생년월일이 입력되지 않아 추천을 생성할 수 없습니다.",
        )

    return await rec_service.recommend_pair(current_user, target, db)
