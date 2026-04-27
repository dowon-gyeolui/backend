"""Seed demo users for the matching showcase.

Idempotent — re-running skips users whose ``kakao_id`` already exists.

Usage
-----
    # local SQLite
    python scripts/seed_demo_users.py

    # production Render PostgreSQL (one-shot, doesn't touch .env)
    $env:DATABASE_URL = "postgresql+asyncpg://..."
    python scripts/seed_demo_users.py

The 10 seeded users span both genders, span ages 23–34, mix daylight and
unknown birth times, and use stable Unsplash portraits so cards render.
``is_paid`` is set True so the matching API doesn't blind their photos when
the calling user (free tier) requests candidates.
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


# Stable, royalty-free Unsplash portrait URLs (sized to match card aspect).
_UNSPLASH = (
    "https://images.unsplash.com/photo-{id}?w=400&h=400&fit=crop"
)


def _photo(uid: str) -> str:
    return _UNSPLASH.format(id=uid)


DEMO_USERS: list[dict] = [
    # ── Female ────────────────────────────────────────────────────────────
    {
        "kakao_id": "demo_f01",
        "nickname": "김민주",
        "gender": "female",
        "birth_date": date(1995, 3, 15),
        "birth_time": "14:30",
        "photo_url": _photo("1494790108377-be9c29b29330"),
    },
    {
        "kakao_id": "demo_f02",
        "nickname": "설윤아",
        "gender": "female",
        "birth_date": date(1999, 8, 22),
        "birth_time": "09:15",
        "photo_url": _photo("1438761681033-6461ffad8d80"),
    },
    {
        "kakao_id": "demo_f03",
        "nickname": "이나경",
        "gender": "female",
        "birth_date": date(1997, 11, 7),
        "birth_time": None,  # 시간 모름
        "photo_url": _photo("1517841905240-472988babdf9"),
    },
    {
        "kakao_id": "demo_f04",
        "nickname": "신시아",
        "gender": "female",
        "birth_date": date(1995, 6, 30),
        "birth_time": "21:45",
        "photo_url": _photo("1531123897727-8f129e1688ce"),
    },
    {
        "kakao_id": "demo_f05",
        "nickname": "박서연",
        "gender": "female",
        "birth_date": date(1998, 2, 11),
        "birth_time": "07:20",
        "photo_url": _photo("1487412720507-e7ab37603c6f"),
    },
    # ── Male ──────────────────────────────────────────────────────────────
    {
        "kakao_id": "demo_m01",
        "nickname": "이준호",
        "gender": "male",
        "birth_date": date(1993, 5, 18),
        "birth_time": "11:00",
        "photo_url": _photo("1500648767791-00dcc994a43e"),
    },
    {
        "kakao_id": "demo_m02",
        "nickname": "박지훈",
        "gender": "male",
        "birth_date": date(1996, 9, 4),
        "birth_time": "16:25",
        "photo_url": _photo("1507003211169-0a1dd7228f2d"),
    },
    {
        "kakao_id": "demo_m03",
        "nickname": "김도현",
        "gender": "male",
        "birth_date": date(2000, 1, 27),
        "birth_time": None,
        "photo_url": _photo("1472099645785-5658abf4ff4e"),
    },
    {
        "kakao_id": "demo_m04",
        "nickname": "최서준",
        "gender": "male",
        "birth_date": date(1994, 12, 12),
        "birth_time": "03:50",
        "photo_url": _photo("1531427186611-ecfd6d936c79"),
    },
    {
        "kakao_id": "demo_m05",
        "nickname": "정우진",
        "gender": "male",
        "birth_date": date(1997, 7, 8),
        "birth_time": "19:35",
        "photo_url": _photo("1506794778202-cad84cf45f1d"),
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