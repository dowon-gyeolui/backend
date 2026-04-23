from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.compatibility import CompatibilityScore, MatchCandidate
from app.services import compatibility as compatibility_service

router = APIRouter()


@router.get("/score/{target_user_id}", response_model=CompatibilityScore)
async def get_compatibility_score(
    target_user_id: int,
    db: AsyncSession = Depends(get_db),
):
    # TODO: Resolve current user from JWT
    return compatibility_service.calculate_score(user_a_id=1, user_b_id=target_user_id)


@router.get("/matches", response_model=list[MatchCandidate])
async def get_matches(db: AsyncSession = Depends(get_db)):
    # TODO: Resolve current user from JWT, query real candidates from DB
    return compatibility_service.get_placeholder_matches(user_id=1)
