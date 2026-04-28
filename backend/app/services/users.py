from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatThread, Message
from app.models.user import User
from app.schemas.user import BirthDataCreate, BirthDataUpdate, ProfileUpdate


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


async def delete_account(user: User, db: AsyncSession) -> None:
    """탈퇴하기 — purge the user's record and any FK-bound rows.

    chat_threads.user_a_id/user_b_id and messages.thread_id/sender_id are
    foreign keys to users.id with no ON DELETE CASCADE configured, so we
    delete the dependent rows manually to avoid IntegrityError.

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

    # 3) Finally, the user.
    await db.delete(user)
    await db.commit()
