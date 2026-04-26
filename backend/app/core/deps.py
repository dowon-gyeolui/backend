"""Request dependencies shared across routers.

Auth: real Kakao OAuth issues a JWT in /auth/kakao/callback. Clients send it
back as ``Authorization: Bearer <token>``; we decode the user_id from the
``sub`` claim and look up the User row.

Dev escape hatch: when ``settings.debug`` is on AND no Authorization header is
present, fall back to the legacy ``X-Dev-User-Id`` header so existing scripts
and Swagger flows keep working without going through Kakao.
"""
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_dev_user_id: int | None = Header(default=None, alias="X-Dev-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        user_id = decode_access_token(token)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    if settings.debug:
        dev_id = x_dev_user_id if x_dev_user_id is not None else 1
        dev_kakao_id = f"dev_{dev_id}"
        result = await db.execute(select(User).where(User.kakao_id == dev_kakao_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(kakao_id=dev_kakao_id)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user

    raise HTTPException(status_code=401, detail="Authentication required")