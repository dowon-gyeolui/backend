"""Quick admin: list all real (non-demo) users with their gender + birth_date.

Useful for spotting users who completed only partial onboarding (e.g.,
gender is null) which would skip them from gender-filtered matching.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file  # noqa: E402

load_env_file(_BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
        for u in rows:
            tag = "DEMO " if u.kakao_id and u.kakao_id.startswith("demo_") else "REAL "
            print(
                f"{tag} id={u.id} kakao={u.kakao_id} nickname={u.nickname} "
                f"gender={u.gender} birth={u.birth_date} time={u.birth_time} "
                f"paid={u.is_paid}"
            )


if __name__ == "__main__":
    asyncio.run(main())