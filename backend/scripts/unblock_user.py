"""Admin: 특정 닉네임 사용자의 차단(user_blocks)을 해제하고 채팅방 left 플래그를 복원."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file  # noqa: E402

load_env_file(_BACKEND_ROOT)

from sqlalchemy import and_, delete, or_, select, update  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.block import UserBlock  # noqa: E402
from app.models.chat import ChatThread  # noqa: E402
from app.models.user import User  # noqa: E402


async def main(nickname: str) -> None:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.nickname == nickname))
        ).scalar_one_or_none()

        if user is None:
            print(f"[ERROR] 닉네임 '{nickname}' 사용자를 찾을 수 없어요.")
            return

        print(f"[INFO] 찾은 유저: id={user.id} nickname={user.nickname} kakao={user.kakao_id}")

        blocks = (
            await db.execute(
                select(UserBlock).where(
                    or_(
                        UserBlock.blocker_id == user.id,
                        UserBlock.blocked_id == user.id,
                    )
                )
            )
        ).scalars().all()

        if blocks:
            for b in blocks:
                print(f"  차단 레코드 삭제: blocker={b.blocker_id} → blocked={b.blocked_id}")
            await db.execute(
                delete(UserBlock).where(
                    or_(
                        UserBlock.blocker_id == user.id,
                        UserBlock.blocked_id == user.id,
                    )
                )
            )
        else:
            print("  user_blocks 레코드 없음.")

        threads = (
            await db.execute(
                select(ChatThread).where(
                    or_(
                        ChatThread.user_a_id == user.id,
                        ChatThread.user_b_id == user.id,
                    )
                )
            )
        ).scalars().all()

        restored = 0
        for t in threads:
            changed = False
            if t.user_a_id == user.id and t.user_a_left:
                t.user_a_left = False
                changed = True
            if t.user_b_id == user.id and t.user_b_left:
                t.user_b_left = False
                changed = True
            if changed:
                print(f"  chat_thread id={t.id} — left 플래그 해제")
                restored += 1

        if restored == 0:
            print("  복원할 chat_thread 없음.")

        await db.commit()
        print(f"[DONE] '{nickname}' 차단 해제 완료.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.unblock_user <닉네임>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
