from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import recommendations as rec_service

router = APIRouter()


@router.get("/me")
async def get_recommendations(db: AsyncSession = Depends(get_db)):
    # TODO: Resolve user from JWT, derive recommendations from saju result
    return rec_service.get_placeholder_recommendations(user_id=1)
