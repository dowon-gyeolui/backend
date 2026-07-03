"""매칭 쇼케이스용 데모 유저(신시아)를 시드하는 idempotent 스크립트."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file  # noqa: E402

load_env_file(_BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal, init_db  # noqa: E402
from app.models.user import User  # noqa: E402


DEMO_USERS: list[dict] = [
    {
        "kakao_id": "demo_f04",
        "nickname": "신시아",
        "gender": "female",
        "birth_date": date(1995, 6, 30),
        "birth_time": "21:45",
        "photo_url": "/cynthia.png",
    },
]


async def seed() -> None:
    await init_db()

    created = 0
    skipped = 0
    async with AsyncSessionLocal() as db:
        for data in DEMO_USERS:
            existing = (
                await db.execute(
                    select(User).where(User.kakao_id == data["kakao_id"])
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue

            user = User(
                kakao_id=data["kakao_id"],
                nickname=data["nickname"],
                gender=data["gender"],
                birth_date=data["birth_date"],
                birth_time=data["birth_time"],
                calendar_type="solar",
                is_leap_month=False,
                photo_url=data["photo_url"],
                is_paid=True,
            )
            db.add(user)
            created += 1

        await db.commit()

    total = len(DEMO_USERS)
    print(f"seeded demo users: total={total} created={created} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(seed())