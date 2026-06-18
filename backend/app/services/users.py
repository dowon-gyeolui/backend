from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card_unlock import CardUnlock
from app.models.chat import ChatThread, Message
from app.models.daily_ai_text import DailyAiText
from app.models.moderation import UserStrike
from app.models.payment import StarOrder
from app.models.photo import UserPhoto
from app.models.report import Report
from app.models.user import User
from app.schemas.user import (
    BirthDataCreate,
    BirthDataUpdate,
    ProfileUpdate,
    PublicProfileResponse,
)
from app.services.storage import delete_image


async def set_birth_data(user: User, data: BirthDataCreate, db: AsyncSession) -> User:
    """Replace all birth data fields on the user (POST semantics)."""
    user.birth_date = data.birth_date
    user.birth_time = data.birth_time
    user.calendar_type = data.calendar_type
    user.is_leap_month = data.is_leap_month
    user.gender = data.gender
    await db.commit()
    await db.refresh(user)
    return user


async def patch_birth_data(user: User, data: BirthDataUpdate, db: AsyncSession) -> User:
    """Update only the provided birth data fields (PATCH semantics)."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def patch_profile(user: User, data: ProfileUpdate, db: AsyncSession) -> User:
    """Update only the provided profile fields (nickname / photo_url)."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def build_public_profile(
    viewer: User, target: User, db: AsyncSession,
) -> PublicProfileResponse:
    from app.services import compatibility as compatibility_service
    from app.services.saju import calculate as calculate_saju
    from app.models.photo import UserPhoto

    is_blinded = not viewer.is_paid

    age = compatibility_service._compute_age(target.birth_date)

    primary_photo = (
        await db.execute(
            select(UserPhoto)
            .where(UserPhoto.user_id == target.id)
            .where(UserPhoto.is_primary.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    is_face_verified = bool(primary_photo and primary_photo.is_face_verified)

    # 상세 페이지 사진 캐러셀용 — blinded 면 공개하지 않는다.
    photos = [] if is_blinded else await compatibility_service._candidate_photos(target, db)

    dominant_ko: str | None = None
    day_pillar: str | None = None
    if target.birth_date is not None:
        try:
            saju = calculate_saju(target)
            dom = compatibility_service._dominant_element(saju.element_profile)
            dominant_ko = (
                compatibility_service._ELEMENT_KO[dom] if dom else None
            )
            day_pillar = saju.pillars[2].combined
        except Exception:
            pass

    score: int | None = None
    if (
        viewer.id != target.id
        and target.birth_date is not None
        and viewer.birth_date is not None
    ):
        try:
            score = compatibility_service.calculate(viewer, target).score
        except Exception:
            score = None

    return PublicProfileResponse(
        id=target.id,
        nickname=target.nickname,
        photo_url=None if is_blinded else target.photo_url,
        photos=photos,
        is_blinded=is_blinded,
        age=age,
        gender=target.gender,
        bio=target.bio,
        height_cm=target.height_cm,
        mbti=target.mbti,
        job=target.job,
        region=target.region,
        smoking=target.smoking,
        drinking=target.drinking,
        religion=target.religion,
        dominant_element=dominant_ko,
        day_pillar=day_pillar,
        compatibility_score=score,
        is_face_verified=is_face_verified,
    )


async def delete_account(user: User, db: AsyncSession) -> None:
    """탈퇴하기 — purge the user's record and any FK-bound rows.

    Several tables FK back to users.id with no ON DELETE CASCADE configured,
    so we have to clean up dependent rows manually before deleting the
    user, otherwise Postgres raises IntegrityError and the request fails
    with what the browser surfaces as "Failed to fetch":

      - chat_threads.user_a_id / user_b_id  + their messages
      - messages.sender_id (defensive)
      - user_photos.user_id
      - card_unlocks.user_id  AND .candidate_id (the user might have
        been someone else's unlocked card, those rows must go too)
      - reports.reporter_id / reported_id
      - daily_ai_texts.user_id (cached 오늘의 인연운/행동가이드 — present
        for nearly every user who has opened the home screen)
      - star_orders.user_id (별 충전 결제 내역)

    Re-registration with the same kakao_id is fine — the unique constraint
    is satisfied once this row is gone.
    """
    # 1) Threads where this user is a participant.
    thread_rows = (
        await db.execute(
            select(ChatThread.id).where(
                or_(ChatThread.user_a_id == user.id, ChatThread.user_b_id == user.id)
            )
        )
    ).scalars().all()

    if thread_rows:
        await db.execute(delete(Message).where(Message.thread_id.in_(thread_rows)))
        await db.execute(delete(ChatThread).where(ChatThread.id.in_(thread_rows)))

    # 2) Messages where the user is the sender on a thread that survived
    #    (defensive — should be covered above but cheap to belt-and-brace).
    await db.execute(delete(Message).where(Message.sender_id == user.id))

    # 3) User photos — also remove from Cloudinary so the assets don't
    #    linger after the account is gone.
    photo_rows = (
        await db.execute(
            select(UserPhoto).where(UserPhoto.user_id == user.id)
        )
    ).scalars().all()
    for photo in photo_rows:
        if photo.public_id:
            delete_image(photo.public_id)
    if photo_rows:
        await db.execute(
            delete(UserPhoto).where(UserPhoto.user_id == user.id)
        )

    # 4) Card unlocks: this user's own unlocks AND any where they were the
    #    unlocked candidate.
    await db.execute(
        delete(CardUnlock).where(
            or_(
                CardUnlock.user_id == user.id,
                CardUnlock.candidate_id == user.id,
            )
        )
    )

    # 5) Reports they filed or that targeted them.
    await db.execute(
        delete(Report).where(
            or_(
                Report.reporter_id == user.id,
                Report.reported_id == user.id,
            )
        )
    )

    # 6) Moderation strike audit log.
    await db.execute(
        delete(UserStrike).where(UserStrike.user_id == user.id)
    )

    # 7) Cached daily AI texts (오늘의 인연운 / 행동 가이드).
    await db.execute(
        delete(DailyAiText).where(DailyAiText.user_id == user.id)
    )

    # 8) Star top-up orders.
    await db.execute(
        delete(StarOrder).where(StarOrder.user_id == user.id)
    )

    # Snapshot the kakao_id BEFORE deleting the row so we can unlink
    # on Kakao's side even after our DB record is gone.
    kakao_id = user.kakao_id

    # 9) Our user row.
    await db.delete(user)
    await db.commit()

    # 10) Tell Kakao the user is gone — without this, the user's "동의 완료"
    #    state on Kakao persists, and a re-signup flows in silently
    #    without showing the consent screen. Best-effort: failure here
    #    doesn't roll back the local delete (already committed above).
    if kakao_id:
        from app.services.auth import unlink_kakao_user
        await unlink_kakao_user(kakao_id)
