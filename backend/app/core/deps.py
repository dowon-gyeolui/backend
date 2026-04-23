"""Request dependencies shared across routers.

Dev auth: real Kakao OAuth is not implemented yet.
Pass `X-Dev-User-Id` header (integer, default 1) to identify the caller.
A placeholder user is auto-created on first use so the endpoints work
immediately via Swagger without any prior setup.

TODO: Replace get_current_user with JWT-based auth once Kakao OAuth is ready.
"""
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User


async def get_current_user(
    x_dev_user_id: int = Header(default=1, alias="X-Dev-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> User:
    dev_kakao_id = f"dev_{x_dev_user_id}"

    result = await db.execute(select(User).where(User.kakao_id == dev_kakao_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(kakao_id=dev_kakao_id)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user
