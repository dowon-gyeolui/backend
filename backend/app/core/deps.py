"""라우터 공용 의존성 — 현재 로그인 사용자 조회.

- Authorization: Bearer <JWT> 헤더를 파싱해 사용자 row 반환
- debug 모드 + Authorization 헤더 없음 → X-Dev-User-Id 헤더로 폴백
  (스크립트/Swagger에서 카카오 로그인 없이 작업할 수 있도록)
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