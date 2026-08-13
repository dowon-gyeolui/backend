"""재화(스타) — 차감이 카드 생성과 같은 트랜잭션인지, 잔액 부족이 막히는지.

OI-PAY-004 확정: "서버에서 카드 생성에 성공했을 때 동일 트랜잭션"에 차감한다.
QA 체크포인트: "버튼 연속 클릭 중복 지급 0건", "잔액 직접 덮어쓰기 금지".
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.card_unlock import CardUnlock
from app.services.matching import STAR_COST_PER_CARD, unlock_extra


@pytest.mark.asyncio
async def test_insufficient_balance_blocks_unlock(db, make_user):
    me = await make_user(kakao_id="me", gender="male", star_balance=STAR_COST_PER_CARD - 1)
    await make_user(kakao_id="c1", gender="female")

    with pytest.raises(HTTPException) as exc:
        await unlock_extra(me, db)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_no_star_is_spent_when_unlock_fails(db, make_user):
    """후보가 없어 실패하면 잔액이 줄면 안 된다."""
    me = await make_user(kakao_id="me", gender="male", star_balance=100)

    with pytest.raises(HTTPException) as exc:
        await unlock_extra(me, db)
    assert exc.value.status_code == 404
    assert me.star_balance == 100


@pytest.mark.asyncio
async def test_successful_unlock_deducts_exactly_once(db, make_user):
    me = await make_user(kakao_id="me", gender="male", star_balance=100)
    await make_user(kakao_id="c1", gender="female")

    await unlock_extra(me, db)

    assert me.star_balance == 100 - STAR_COST_PER_CARD
    count = await db.execute(select(func.count(CardUnlock.id)).where(CardUnlock.user_id == me.id))
    assert count.scalar_one() == 1


@pytest.mark.asyncio
async def test_balance_never_goes_negative(db, make_user):
    """스타가 정확히 1장분보다 적을 때 반복 시도해도 음수가 되지 않는다."""
    me = await make_user(kakao_id="me", gender="male", star_balance=STAR_COST_PER_CARD)
    await make_user(kakao_id="c1", gender="female")
    await make_user(kakao_id="c2", gender="female")

    await unlock_extra(me, db)
    assert me.star_balance == 0

    with pytest.raises(HTTPException):
        await unlock_extra(me, db)
    assert me.star_balance == 0
