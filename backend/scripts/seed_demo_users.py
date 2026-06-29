"""Seed demo users for the matching showcase.

Idempotent — re-running skips users whose ``kakao_id`` already exists.

Usage
-----
    # local SQLite
    python scripts/seed_demo_users.py

    # production Render PostgreSQL (one-shot, doesn't touch .env)
    $env:DATABASE_URL = "postgresql+asyncpg://..."
    python scripts/seed_demo_users.py

Seeds a single demo user (신시아) using the local ``/cynthia.png`` portrait so
cards render. ``is_paid`` is set True so the matching API doesn't blind the
photo when the calling user (free tier) requests candidates.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

# Allow importing app.* when run from anywhere
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
    # Ensure schema exists (no-op on already-migrated DB)
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
                is_paid=True,  # show photos in matches without blind policy
            )
            db.add(user)
            created += 1

        await db.commit()

    total = len(DEMO_USERS)
    print(f"seeded demo users: total={total} created={created} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(seed())