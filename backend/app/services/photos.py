"""사용자 사진 갤러리 등록/삭제/대표사진 지정 서비스."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.photo import UserPhoto
from app.models.user import User
from app.services.storage import delete_image

MAX_PHOTOS_PER_USER = 6


async def list_photos(user: User, db: AsyncSession) -> list[UserPhoto]:
    rows = (
        await db.execute(
            select(UserPhoto)
            .where(UserPhoto.user_id == user.id)
            .order_by(UserPhoto.position.asc(), UserPhoto.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def add_photo(
    user: User,
    *,
    url: str,
    public_id: str,
    db: AsyncSession,
    is_face_verified: bool = True,
) -> UserPhoto:
    existing = await list_photos(user, db)
    is_primary = len(existing) == 0
    next_position = max((p.position for p in existing), default=-1) + 1

    photo = UserPhoto(
        user_id=user.id,
        url=url,
        public_id=public_id,
        position=next_position,
        is_primary=is_primary,
        is_face_verified=is_face_verified,
    )
    db.add(photo)

    if is_primary:
        user.photo_url = url

    await db.commit()
    await db.refresh(photo)
    return photo


async def delete_photo(
    user: User, photo_id: int, db: AsyncSession
) -> bool:
    photo = await db.get(UserPhoto, photo_id)
    if photo is None or photo.user_id != user.id:
        return False

    was_primary = bool(photo.is_primary)
    public_id = photo.public_id or ""

    await db.delete(photo)

    if was_primary:
        replacement = (
            await db.execute(
                select(UserPhoto)
                .where(UserPhoto.user_id == user.id)
                .order_by(UserPhoto.position.asc(), UserPhoto.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if replacement is not None:
            replacement.is_primary = True
            user.photo_url = replacement.url
        else:
            user.photo_url = None

    await db.commit()

    delete_image(public_id)
    return True


async def set_primary(
    user: User, photo_id: int, db: AsyncSession
) -> UserPhoto | None:
    photo = await db.get(UserPhoto, photo_id)
    if photo is None or photo.user_id != user.id:
        return None

    await db.execute(
        update(UserPhoto)
        .where(UserPhoto.user_id == user.id)
        .where(UserPhoto.id != photo.id)
        .values(is_primary=False)
    )
    photo.is_primary = True
    user.photo_url = photo.url

    await db.commit()
    await db.refresh(photo)
    return photo


def primary_photo_url(photos: list[UserPhoto]) -> str | None:
    for p in photos:
        if p.is_primary:
            return p.url
    return photos[0].url if photos else None