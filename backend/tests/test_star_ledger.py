"""재화 원장(T-B06) — 멱등성·음수 잔액 제약.

QA 체크포인트 3개에 각각 대응한다.
- "버튼 연속 클릭 중복 지급 0건" → 같은 멱등키 재요청이 원장 행을 늘리지 않는다.
- "전후잔액 연속성"           → 원장의 `balance_after` 가 끊기지 않고 잔액과 일치한다.
- "잔액 직접 덮어쓰기 금지"    → 잔액을 쓰는 코드가 원장 서비스 밖에 없다(래칫).

동시 요청은 SQLite in-memory + StaticPool(커넥션 1개) 에서 재현할 수 없다. 그래서
**멱등성을 지키는 주체가 DB 제약이라는 사실 자체**를 검증한다 — 서비스를 우회해
같은 멱등키를 직접 INSERT 하면 IntegrityError 로 거부되어야 한다. 이게 참이면
경쟁하는 두 트랜잭션 중 하나는 반드시 실패한다.
"""

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models.card_unlock import CardUnlock
from app.models.payment import STATUS_PAID, STATUS_PENDING, StarOrder
from app.models.star_ledger import (
    ENTRY_CARD_UNLOCK,
    ENTRY_PURCHASE,
    StarLedger,
)
from app.models.user import User
from app.services import payments as payments_service
from app.services import star_ledger
from app.services.matching import STAR_COST_PER_CARD, unlock_extra

APP_DIR = Path(__file__).resolve().parents[1] / "app"


async def _entries(db, user) -> list[StarLedger]:
    rows = await db.execute(
        select(StarLedger).where(StarLedger.user_id == user.id).order_by(StarLedger.id)
    )
    return list(rows.scalars().all())


# --- 멱등성 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_reference_grants_only_once(db, make_user):
    """같은 (주문번호, 지급유형) 으로 두 번 지급해도 원장 1행·잔액 1회."""
    me = await make_user(kakao_id="me", star_balance=0)

    assert await star_ledger.record(
        db, me, entry_type=ENTRY_PURCHASE, reference_id="order-1", amount=50
    ) is True
    assert await star_ledger.record(
        db, me, entry_type=ENTRY_PURCHASE, reference_id="order-1", amount=50
    ) is False
    await db.commit()

    assert me.star_balance == 50
    assert len(await _entries(db, me)) == 1


@pytest.mark.asyncio
async def test_same_reference_different_type_is_a_separate_entry(db, make_user):
    """멱등키는 주문번호 단독이 아니라 (주문번호 + 지급유형) 이다."""
    me = await make_user(kakao_id="me", star_balance=0)

    await star_ledger.record(
        db, me, entry_type=ENTRY_PURCHASE, reference_id="ref-1", amount=30
    )
    await star_ledger.record(
        db, me, entry_type=ENTRY_CARD_UNLOCK, reference_id="ref-1", amount=-10
    )
    await db.commit()

    assert me.star_balance == 20
    assert len(await _entries(db, me)) == 2


@pytest.mark.asyncio
async def test_duplicate_key_is_rejected_by_db_not_by_python(db, make_user):
    """서비스를 우회해도 DB 가 막는다 — 동시 요청에서 이중 지급이 불가능한 근거."""
    me = await make_user(kakao_id="me", star_balance=0)
    await star_ledger.record(
        db, me, entry_type=ENTRY_PURCHASE, reference_id="order-1", amount=50
    )
    await db.commit()

    db.add(
        StarLedger(
            user_id=me.id,
            entry_type=ENTRY_PURCHASE,
            reference_id="order-1",
            amount=50,
            balance_after=100,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_repeated_confirm_grants_once(db, make_user, monkeypatch):
    """결제 승인 경로 통합 — 승인 요청이 두 번 와도 스타는 한 번만 들어온다."""

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(payments_service.settings, "toss_secret_key", "toss-dummy")
    monkeypatch.setattr(payments_service.httpx, "AsyncClient", _Client)

    me = await make_user(kakao_id="me", star_balance=0)
    db.add(
        StarOrder(
            user_id=me.id,
            order_id="order-abc",
            product_id="STAR-002",
            amount=5500,
            star_amount=50,
            status=STATUS_PENDING,
        )
    )
    await db.commit()

    await payments_service.confirm_payment(me, "pay-key", "order-abc", 5500, db)
    await payments_service.confirm_payment(me, "pay-key", "order-abc", 5500, db)

    assert me.star_balance == 50
    entries = await _entries(db, me)
    assert [(e.entry_type, e.reference_id, e.amount) for e in entries] == [
        (ENTRY_PURCHASE, "order-abc", 50)
    ]

    order = await db.execute(
        select(StarOrder).where(StarOrder.order_id == "order-abc")
    )
    assert order.scalar_one().status == STATUS_PAID


# --- 음수 잔액 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_overdraft_is_refused_and_leaves_no_entry(db, make_user):
    me = await make_user(kakao_id="me", star_balance=5)

    with pytest.raises(HTTPException) as exc:
        await star_ledger.record(
            db, me, entry_type=ENTRY_CARD_UNLOCK, reference_id="c-1", amount=-6
        )
    assert exc.value.status_code == 402

    assert me.star_balance == 5
    assert await _entries(db, me) == []


@pytest.mark.asyncio
async def test_negative_balance_is_rejected_by_db(db, make_user):
    """앱 레이어를 통째로 우회해도 잔액은 음수가 될 수 없다."""
    me = await make_user(kakao_id="me", star_balance=5)

    with pytest.raises(IntegrityError):
        await db.execute(update(User).where(User.id == me.id).values(star_balance=-1))


@pytest.mark.asyncio
async def test_negative_balance_after_is_rejected_by_db(db, make_user):
    me = await make_user(kakao_id="me", star_balance=0)

    db.add(
        StarLedger(
            user_id=me.id,
            entry_type=ENTRY_CARD_UNLOCK,
            reference_id="c-1",
            amount=-1,
            balance_after=-1,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


# --- 전후잔액 연속성 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_after_is_continuous_and_matches_balance(db, make_user):
    me = await make_user(kakao_id="me", star_balance=0)

    for i, amount in enumerate([100, -10, -10, 50, -30]):
        entry_type = ENTRY_PURCHASE if amount > 0 else ENTRY_CARD_UNLOCK
        await star_ledger.record(
            db, me, entry_type=entry_type, reference_id=f"ref-{i}", amount=amount
        )
    await db.commit()

    running = 0
    for entry in await _entries(db, me):
        running += entry.amount
        assert entry.balance_after == running, "전후잔액이 끊겼다"

    assert me.star_balance == running == 100
    total = await db.execute(
        select(func.sum(StarLedger.amount)).where(StarLedger.user_id == me.id)
    )
    assert total.scalar_one() == me.star_balance


# --- 카드 열람 차감 (OI-PAY-004: 카드 생성과 동일 트랜잭션) -------------------------


@pytest.mark.asyncio
async def test_unlock_extra_records_ledger_entry(db, make_user):
    me = await make_user(kakao_id="me", gender="male", star_balance=100)
    candidate = await make_user(kakao_id="c1", gender="female")

    await unlock_extra(me, db)

    entries = await _entries(db, me)
    assert len(entries) == 1
    assert entries[0].entry_type == ENTRY_CARD_UNLOCK
    assert entries[0].reference_id == f"{me.id}:{candidate.id}"
    assert entries[0].amount == -STAR_COST_PER_CARD
    assert entries[0].balance_after == me.star_balance == 100 - STAR_COST_PER_CARD


@pytest.mark.asyncio
async def test_no_ledger_entry_when_card_cannot_be_created(db, make_user):
    """후보가 없어 카드를 못 만들면 차감도 없어야 한다(동일 트랜잭션)."""
    me = await make_user(kakao_id="me", gender="male", star_balance=100)

    with pytest.raises(HTTPException):
        await unlock_extra(me, db)

    assert me.star_balance == 100
    assert await _entries(db, me) == []
    count = await db.execute(
        select(func.count(CardUnlock.id)).where(CardUnlock.user_id == me.id)
    )
    assert count.scalar_one() == 0


# --- 래칫: 잔액 직접 덮어쓰기 금지 -------------------------------------------------


def test_balance_is_written_only_through_the_ledger():
    """`user.star_balance = ...` 를 원장 서비스 밖에서 하면 실패한다.

    잔액과 원장이 어긋나면 대사(對査)가 불가능해지고, 관리자 화면(ADM-WALLET-001)이
    보여줄 수 있는 진실이 사라진다. 새 지급/차감 경로는 반드시 `star_ledger.record()`
    를 거칠 것.
    """
    allowed = APP_DIR / "services" / "star_ledger.py"
    pattern = re.compile(r"\.star_balance\s*[-+]?=[^=]")

    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{lineno}"
        for path in APP_DIR.rglob("*.py")
        if path != allowed
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"잔액을 원장 밖에서 쓰고 있다: {offenders}"
