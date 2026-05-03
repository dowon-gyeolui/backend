"""User photo gallery service.

Backs `/users/me/photos` endpoints. Keeps the legacy users.photo_url in
sync with whichever photo is flagged primary so existing match-card /
public-profile callers don't have to be rewritten.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.photo import UserPhoto
from app.models.user import User
from app.services.storage import delete_image


# Hard-cap so a user can't fill our Cloudinary quota with one account.
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
    """Append a new photo. The first photo becomes primary automatically.

    `is_face_verified` defaults to True because under our strict (option B)
    policy, photo_moderation rejects non-face uploads BEFORE this function
    is called — anything that reaches here has passed strict face check.
    Legacy callers can pass False if needed.

    Caller is responsible for enforcing MAX_PHOTOS_PER_USER (router does
    so before invoking the upload to avoid a wasted Cloudinary call).
    """
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
        # Mirror to users.photo_url so legacy match-card paths keep working
        # without joining the new gallery table.
        user.photo_url = url

    await db.commit()
    await db.refresh(photo)
    return photo


async def delete_photo(
    user: User, photo_id: int, db: AsyncSession
) -> bool:
    """Remove a photo from the gallery. If it was primary, promote the
    next remaining photo (lowest position) to primary so the user always
    has a main photo when the gallery is non-empty.
    """
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

    # Best-effort Cloudinary delete after the DB transaction commits — if
    # the request crashes after this point we'd rather have an orphan
    # asset than a stuck DB row.
    delete_image(public_id)
    return True


async def set_primary(
    user: User, photo_id: int, db: AsyncSession
) -> UserPhoto | None:
    """Promote `photo_id` to primary. Demotes any other primary atomically."""
    photo = await db.get(UserPhoto, photo_id)
    if photo is None or photo.user_id != user.id:
        return None

    # Demote everyone else, promote this one.
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