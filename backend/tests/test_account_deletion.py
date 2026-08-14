"""탈퇴(hard delete) 회귀 — 자식 행이 남아 FK 위반으로 탈퇴가 실패하지 않는가.

`device_tokens` 만 정리 대상에서 빠져 있어, 푸시 토큰을 등록한 계정(=앱을 실제로
쓴 계정 대부분)의 탈퇴가 `ForeignKeyViolation` 으로 실패했다(T-B07). 탈퇴가 터지면
그 뒤에 오는 카카오 unlink(PIPA 대응)도 실행되지 않는다.

이 테스트가 의미를 가지려면 SQLite FK 강제가 켜져 있어야 한다(`conftest.engine`).
꺼져 있으면 고아 행이 그대로 남은 채 DELETE 가 조용히 성공한다.
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.block import UserBlock
from app.models.card_unlock import CardUnlock
from app.models.chat import ChatThread, Message
from app.models.daily_ai_text import DailyAiText
from app.models.device_token import DeviceToken
from app.models.interview import InterviewAnswer
from app.models.moderation import UserStrike
from app.models.payment import StarOrder
from app.models.photo import UserPhoto
from app.models.report import Report
from app.models.user import User
from app.services.users import delete_account


async def _make_all_child_rows(db, me: User, peer: User) -> None:
    """`users.id` 를 참조하는 모든 테이블에 이 사용자의 행을 하나씩 만든다."""
    thread = ChatThread(user_a_id=me.id, user_b_id=peer.id)
    db.add(thread)
    await db.flush()

    db.add_all(
        [
            Message(thread_id=thread.id, sender_id=me.id, content="안녕하세요"),
            # public_id 가 있으면 Cloudinary 삭제를 호출하므로 None 으로 둔다.
            UserPhoto(user_id=me.id, url="https://example.test/p.jpg"),
            CardUnlock(user_id=me.id, candidate_id=peer.id, kind="daily"),
            Report(reporter_id=me.id, reported_id=peer.id, reason="spam"),
            UserStrike(user_id=me.id, kind="chat"),
            DailyAiText(
                user_id=me.id,
                kst_date=date(2026, 8, 14),
                kind="action_guide",
                text="오늘의 조언",
            ),
            InterviewAnswer(user_id=me.id, question_key="hobby", answer="등산"),
            UserBlock(blocker_id=me.id, blocked_id=peer.id),
            StarOrder(
                user_id=me.id,
                order_id="order-1",
                product_id="star_10",
                amount=1000,
                star_amount=10,
            ),
            DeviceToken(user_id=me.id, token="fcm-token-1", platform="android"),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_delete_account_succeeds_with_device_token(db, make_user):
    """기기 토큰이 등록된 계정의 탈퇴 — T-B07 의 재현 케이스."""
    me = await make_user(kakao_id="leaver", gender="male")
    db.add(DeviceToken(user_id=me.id, token="fcm-token-1", platform="android"))
    await db.commit()

    await delete_account(me, db)

    assert (
        await db.execute(select(func.count(User.id)).where(User.id == me.id))
    ).scalar_one() == 0
    assert (
        await db.execute(
            select(func.count(DeviceToken.id)).where(DeviceToken.user_id == me.id)
        )
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_delete_account_leaves_no_orphan_rows(db, make_user):
    """모든 자식 테이블에 행이 있어도 탈퇴가 되고, 그 사용자를 가리키는 행이 남지 않는다.

    새 테이블이 `users.id` 를 참조하게 되면 `delete_account` 에도 추가해야 한다는 것을
    이 테스트가 강제한다. 스키마 메타데이터를 직접 훑으므로 테이블이 늘어도 따라온다.
    """
    me = await make_user(kakao_id="leaver", gender="male")
    peer = await make_user(kakao_id="peer", gender="female")
    me_id = me.id
    await _make_all_child_rows(db, me, peer)

    await delete_account(me, db)

    from app.database import Base

    leftovers = []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name != "users":
                continue
            column = fk.parent
            count = (
                await db.execute(
                    select(func.count()).select_from(table).where(column == me_id)
                )
            ).scalar_one()
            if count:
                leftovers.append(f"{table.name}.{column.name}={count}행")

    assert not leftovers, f"탈퇴 후 남은 행: {', '.join(leftovers)}"
