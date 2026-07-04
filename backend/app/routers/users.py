"""사용자 프로필/사진/인터뷰 답변/생년월일/계정 삭제 엔드포인트."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import hash_password
from app.database import get_db
from app.models.interview import InterviewAnswer
from app.models.user import User
from app.schemas.photo import UserPhotoListResponse, UserPhotoResponse
from app.schemas.user import (
    BirthDataCreate,
    BirthDataUpdate,
    CredentialsCreate,
    CredentialsResponse,
    InterviewAnswerOut,
    InterviewAnswersUpdate,
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

@router.post("/me/credentials", response_model=CredentialsResponse)
async def set_credentials(
    data: CredentialsCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = await db.execute(
        select(User.id).where(
            User.username == data.username, User.id != current_user.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 아이디입니다.",
        )

    current_user.username = data.username
    current_user.password_hash = hash_password(data.password)
    await db.commit()
    return CredentialsResponse(username=current_user.username)


@router.get("/{user_id}/public-profile", response_model=PublicProfileResponse)
async def get_public_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={user_id} 를 찾을 수 없습니다.",
        )
    return await users_service.build_public_profile(current_user, target, db)


@router.get("/me/interview", response_model=list[InterviewAnswerOut])
async def get_my_interview_answers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """내 연애 인터뷰 답변 — 작성/수정 화면 prefill 용."""
    rows = (
        await db.execute(
            select(InterviewAnswer)
            .where(InterviewAnswer.user_id == current_user.id)
            .order_by(InterviewAnswer.id.asc())
        )
    ).scalars().all()
    return [
        InterviewAnswerOut(question_key=r.question_key, answer=r.answer)
        for r in rows
    ]


@router.put("/me/interview", status_code=status.HTTP_204_NO_CONTENT)
async def replace_interview_answers(
    body: InterviewAnswersUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """연애 인터뷰 답변 전체 교체 — 온보딩 마지막 단계에서 호출.

    기존 답변을 모두 지우고 새로 받은 답변(빈 답변은 제외)으로 대체한다.
    """
    await db.execute(
        delete(InterviewAnswer).where(InterviewAnswer.user_id == current_user.id)
    )
    seen: set[str] = set()
    for item in body.answers:
        text = (item.answer or "").strip()
        key = item.question_key.strip()
        if not text or not key or key in seen:
            continue
        seen.add(key)
        db.add(
            InterviewAnswer(
                user_id=current_user.id,
                question_key=key[:40],
                answer=text[:500],
            )
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


_MAX_PHOTO_BYTES = 15 * 1024 * 1024

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/avif",
    "image/gif",
}


@router.post("/me/photo", response_model=UserProfileResponse)
async def upload_my_photo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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

    existing = await photos_service.list_photos(current_user, db)
    legacy = next(
        (p for p in existing if p.public_id and p.public_id.endswith(f"user_{current_user.id}")),
        None,
    )
    if legacy is not None:
        legacy.url = result["url"]
        for other in existing:
            other.is_primary = other.id == legacy.id
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
        result = upload_image_full(raw)
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
    current_user.is_paid = True
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await users_service.delete_account(current_user, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
