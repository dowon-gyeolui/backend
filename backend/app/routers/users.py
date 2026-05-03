from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.photo import UserPhotoListResponse, UserPhotoResponse
from app.schemas.user import (
    BirthDataCreate,
    BirthDataUpdate,
    ProfileUpdate,
    PublicProfileResponse,
    UserProfileResponse,
)
from app.services import photos as photos_service
from app.services import users as users_service
from app.services.photo_moderation import verify_profile_photo
from app.services.storage import (
    StorageNotConfiguredError,
    upload_image_full,
)

router = APIRouter()


@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/{user_id}/public-profile", response_model=PublicProfileResponse)
async def get_public_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """매칭 카드 → 상세 정보 페이지의 백엔드.

    카카오 ID·정확한 생년월일 같은 민감 정보는 빼고, 사진은 무료 티어
    호출자에 한해 가린다(is_blinded=True). 자기 자신 ID 도 허용해서
    프론트가 같은 컴포넌트를 재활용할 수 있게 한다.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={user_id} 를 찾을 수 없습니다.",
        )
    return await users_service.build_public_profile(current_user, target, db)


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


# Hard-cap profile photos at 15 MB. 8 MB rejected modern Galaxy/Pixel
# 50 MP shots straight from the gallery; 15 MB still leaves headroom
# vs Cloudinary free-tier bandwidth without forcing users to compress.
_MAX_PHOTO_BYTES = 15 * 1024 * 1024

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/avif",  # newer Pixel / OnePlus / Samsung browsers
    "image/gif",   # screenshots from some Android skins
}


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

    # Run face + NSFW moderation BEFORE we waste a Cloudinary upload on
    # a photo we'd just have to delete. The check is cheap (~2¢ KRW per
    # photo) and fails fast on obviously unusable shots.
    moderation = verify_profile_photo(raw)
    if not moderation.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=moderation.reason or "사진 검증에 실패했어요.",
        )

    try:
        result = upload_image_full(raw, public_id=f"user_{current_user.id}")
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

    current_user.photo_url = result["url"]

    # Mirror into the gallery so old single-photo uploads show up in the
    # new gallery modal. We update an existing row when this user already
    # has a "user_{id}" entry rather than creating duplicates each call.
    existing = await photos_service.list_photos(current_user, db)
    legacy = next(
        (p for p in existing if p.public_id and p.public_id.endswith(f"user_{current_user.id}")),
        None,
    )
    if legacy is not None:
        legacy.url = result["url"]
        # promote it to primary so callers see it in match cards
        for other in existing:
            other.is_primary = other.id == legacy.id
        # Re-uploaded under strict moderation → mark verified.
        legacy.is_face_verified = True
        await db.commit()
        await db.refresh(current_user)
        return current_user

    await photos_service.add_photo(
        current_user,
        url=result["url"],
        public_id=result["public_id"],
        db=db,
    )
    await db.refresh(current_user)
    return current_user


# --- Multi-photo gallery ------------------------------------------------
#
# /me/photo (singular) above stays as the legacy endpoint that overwrites
# users.photo_url directly. The endpoints below back the new gallery —
# users can upload up to MAX_PHOTOS_PER_USER, delete any of them, and
# choose which one is the primary photo shown in match cards.

def _photo_response(photo) -> UserPhotoResponse:
    return UserPhotoResponse.model_validate(photo, from_attributes=True)


@router.get("/me/photos", response_model=UserPhotoListResponse)
async def list_my_photos(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = await photos_service.list_photos(current_user, db)
    return UserPhotoListResponse(
        photos=[_photo_response(p) for p in rows],
        primary_photo_url=photos_service.primary_photo_url(rows),
    )


@router.post("/me/photos", response_model=UserPhotoResponse)
async def upload_my_photo_to_gallery(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append a photo to the user's gallery. First upload becomes primary."""
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

    existing = await photos_service.list_photos(current_user, db)
    if len(existing) >= photos_service.MAX_PHOTOS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"사진은 최대 {photos_service.MAX_PHOTOS_PER_USER}장까지 "
                "등록 가능합니다."
            ),
        )

    moderation = verify_profile_photo(raw)
    if not moderation.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=moderation.reason or "사진 검증에 실패했어요.",
        )

    try:
        result = upload_image_full(raw)  # auto-generated public_id
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

    photo = await photos_service.add_photo(
        current_user,
        url=result["url"],
        public_id=result["public_id"],
        db=db,
    )
    return _photo_response(photo)


@router.delete("/me/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = await photos_service.delete_photo(current_user, photo_id, db)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"photo_id={photo_id} 를 찾을 수 없습니다.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/me/photos/{photo_id}/primary", response_model=UserPhotoResponse)
async def set_my_primary_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    photo = await photos_service.set_primary(current_user, photo_id, db)
    if photo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"photo_id={photo_id} 를 찾을 수 없습니다.",
        )
    return _photo_response(photo)


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
