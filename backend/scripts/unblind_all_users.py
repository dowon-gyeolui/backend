"""One-shot: set is_paid=True for every user.

Demo helper. The matching API blinds candidate photos when the requester is
unpaid; for showcase purposes we want everyone to see everything. Re-run
safely — idempotent.
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

from sqlalchemy import update  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(User).where(User.is_paid.is_(False)).values(is_paid=True)
        )
        await db.commit()
        print(f"updated {result.rowcount} users to is_paid=True")


if __name__ == "__main__":
    asyncio.run(main())