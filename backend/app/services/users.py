from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import BirthDataCreate, BirthDataUpdate


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
