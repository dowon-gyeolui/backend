from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.user import BirthDataCreate, BirthDataUpdate, UserProfileResponse
from app.services import users as users_service

router = APIRouter()


@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/me/birth-data", response_model=UserProfileResponse)
async def set_birth_data(
    data: BirthDataCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await users_service.set_birth_data(current_user, data, db)


@router.patch("/me/birth-data", response_model=UserProfileResponse)
async def patch_birth_data(
    data: BirthDataUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await users_service.patch_birth_data(current_user, data, db)
