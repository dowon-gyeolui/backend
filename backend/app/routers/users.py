from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    BirthDataCreate,
    BirthDataUpdate,
    ProfileUpdate,
    UserProfileResponse,
)
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


@router.patch("/me/profile", response_model=UserProfileResponse)
async def patch_profile(
    data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await users_service.patch_profile(current_user, data, db)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """탈퇴하기 — 사용자 계정과 관련 채팅 데이터 삭제.

    동일 kakao_id 로 재가입할 수 있도록 row 자체를 제거합니다. 클라이언트는
    응답을 받은 즉시 토큰을 폐기해야 합니다 (그 토큰의 user_id 는 더 이상
    DB 에 존재하지 않으므로 이후 API 호출은 401 로 거절됩니다).
    """
    await users_service.delete_account(current_user, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
