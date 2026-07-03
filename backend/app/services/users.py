"""회원 프로필/생년월일/탈퇴 관련 서비스 로직."""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card_unlock import CardUnlock
from app.models.chat import ChatThread, Message
from app.models.daily_ai_text import DailyAiText
from app.models.interview import InterviewAnswer
from app.models.moderation import UserStrike
from app.models.payment import StarOrder
from app.models.photo import UserPhoto
from app.models.report import Report
from app.models.user import User
from app.schemas.user import (
    BirthDataCreate,
    BirthDataUpdate,
    InterviewAnswerOut,
    ProfileUpdate,
    PublicProfileResponse,
)
from app.services.storage import delete_image


async def set_birth_data(user: User, data: BirthDataCreate, db: AsyncSession) -> User:
    user.birth_date = data.birth_date
    user.birth_time = data.birth_time
    user.calendar_type = data.calendar_type
    user.is_leap_month = data.is_leap_month
    user.gender = data.gender
    await db.commit()
    await db.refresh(user)
    return user


async def patch_birth_data(user: User, data: BirthDataUpdate, db: AsyncSession) -> User:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def patch_profile(user: User, data: ProfileUpdate, db: AsyncSession) -> User:
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

    photos = [] if is_blinded else await compatibility_service._candidate_photos(target, db)

    target_answers = (
        await db.execute(
            select(InterviewAnswer)
            .where(InterviewAnswer.user_id == target.id)
            .order_by(InterviewAnswer.id.asc())
        )
    ).scalars().all()
    interview_total = len(target_answers)
    if viewer.id == target.id:
        visible_n = interview_total
    else:
        viewer_count = (
            await db.execute(
                select(func.count(InterviewAnswer.id)).where(
                    InterviewAnswer.user_id == viewer.id
                )
            )
        ).scalar_one() or 0
        visible_n = min(viewer_count, interview_total)
    interview_answers = [
        InterviewAnswerOut(question_key=a.question_key, answer=a.answer)
        for a in target_answers[:visible_n]
    ]

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
        interview_answers=interview_answers,
        interview_total=interview_total,
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

    await db.execute(delete(Message).where(Message.sender_id == user.id))

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

    await db.execute(
        delete(CardUnlock).where(
            or_(
                CardUnlock.user_id == user.id,
                CardUnlock.candidate_id == user.id,
            )
        )
    )

    await db.execute(
        delete(Report).where(
            or_(
                Report.reporter_id == user.id,
                Report.reported_id == user.id,
            )
        )
    )

    await db.execute(
        delete(UserStrike).where(UserStrike.user_id == user.id)
    )

    await db.execute(
        delete(DailyAiText).where(DailyAiText.user_id == user.id)
    )

    await db.execute(
        delete(StarOrder).where(StarOrder.user_id == user.id)
    )

    kakao_id = user.kakao_id

    await db.delete(user)
    await db.commit()

    if kakao_id:
        from app.services.auth import unlink_kakao_user
        await unlink_kakao_user(kakao_id)
