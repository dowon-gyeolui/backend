from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.stats import HomeStats
from app.services import stats as stats_service

router = APIRouter()


@router.get("/home", response_model=HomeStats)
async def get_home_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """홈 전광판용 통계 — 단순 집계 + viewer 개인화 사주 분포."""
    return await stats_service.home_stats(current_user, db)
