from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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
from app.services.storage import StorageNotConfiguredError, upload_image

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


# Hard-cap profile photos at 8 MB. Cloudinary's free tier has bandwidth
# limits and there's no upside to letting users upload 50 MB selfies.
_MAX_PHOTO_BYTES = 8 * 1024 * 1024

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@router.post("/me/photo", response_model=UserProfileResponse)
async def upload_my_photo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """프로필 사진 업로드 — Cloudinary 로 올리고 photo_url 갱신.

    multipart/form-data 의 `file` 필드로 이미지 파일을 받는다. 8MB 초과,
    이미지가 아닌 MIME, 또는 Cloudinary 자격증명 미설정 시 4xx/5xx 반환.
    """
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지 형식만 업로드 가능합니다 (받은 형식: {file.content_type}).",
        )

    raw = await file.read()
    if len(raw) > _MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다. {_MAX_PHOTO_BYTES // (1024 * 1024)}MB 이하로 올려주세요.",
        )

    try:
        url = upload_image(raw, public_id=f"user_{current_user.id}")
    except StorageNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"이미지 호스팅 서버 오류: {e}",
        ) from e

    current_user.photo_url = url
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/upgrade-demo", response_model=UserProfileResponse)
async def upgrade_demo(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데모 결제 — is_paid 플래그를 True 로 토글합니다.

    실제 PG (PortOne / Toss) 연동 전까지 사용. 사용자가 매칭 모달의 결제
    버튼을 누르면 호출되며, 다음 /compatibility/matches 응답에서
    is_blinded=False 가 되어 사진 블러가 풀립니다.
    """
    current_user.is_paid = True
    await db.commit()
    await db.refresh(current_user)
    return current_user


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
