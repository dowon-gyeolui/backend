"""결제 이탈 복구(T-D02) — 결제 도중 앱이 죽은 주문을 재진입 시 정리한다.

시나리오는 늘 같다. 사용자가 토스 결제창까지 갔는데 그 사이 OS 가 앱을 죽인다.
승인 요청(`confirm_payment`)이 서버에 닿지 않아 주문이 PENDING 으로 남고,
돈은 나갔는데 스타는 없는 상태가 된다.

**결제사 상태가 원천이다(OI-PAY-001).** 그래서 재진입 때 토스에 주문번호로 되묻고
그 답대로만 움직인다. 지급은 T-B06 의 멱등키(주문번호 + purchase)를 그대로 쓰므로
승인 경로와 겹쳐 들어와도 두 번 들어가지 않는다.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.payment import (
    STATUS_FAILED,
    STATUS_PAID,
    STATUS_PENDING,
    StarOrder,
)
from app.models.star_ledger import ENTRY_PURCHASE, StarLedger
from app.services import payments as payments_service
from app.services import star_ledger


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _fake_client(answers: dict[str, tuple[int, dict]], *, offline: bool = False):
    """주문번호 → (HTTP 상태, 본문) 표를 그대로 돌려주는 가짜 httpx 클라이언트."""

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            if offline:
                raise httpx.ConnectError("네트워크 없음")
            order_id = url.rsplit("/", 1)[-1]
            status_code, payload = answers[order_id]
            return _FakeResponse(status_code, payload)

    return _Client


@pytest.fixture
def toss(monkeypatch):
    """토스 응답을 갈아끼운다. `toss({...})` 로 주문별 답을 정한다."""

    monkeypatch.setattr(payments_service.settings, "toss_secret_key", "toss-dummy")

    def _set(answers: dict[str, tuple[int, dict]], *, offline: bool = False):
        monkeypatch.setattr(
            payments_service.httpx, "AsyncClient", _fake_client(answers, offline=offline)
        )

    return _set


async def _pending_order(
    db,
    user,
    *,
    order_id: str = "order-1",
    amount: int = 5500,
    stars: int = 50,
    age: timedelta = timedelta(0),
) -> StarOrder:
    order = StarOrder(
        user_id=user.id,
        order_id=order_id,
        product_id="STAR-002",
        amount=amount,
        star_amount=stars,
        status=STATUS_PENDING,
        created_at=datetime.now(timezone.utc) - age,
    )
    db.add(order)
    await db.commit()
    return order


async def _status_of(db, order_id: str) -> str:
    row = await db.execute(select(StarOrder).where(StarOrder.order_id == order_id))
    return row.scalar_one().status


async def _entries(db, user) -> list[StarLedger]:
    rows = await db.execute(
        select(StarLedger).where(StarLedger.user_id == user.id).order_by(StarLedger.id)
    )
    return list(rows.scalars().all())


# --- 성공한 결제였던 경우 --------------------------------------------------------


@pytest.mark.asyncio
async def test_paid_order_is_credited_on_restart(db, make_user, toss):
    """승인이 오지 않았어도 토스가 DONE 이면 재진입 시 스타가 들어온다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me)
    toss({"order-1": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-1"})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result == {"star_balance": 50, "credited_stars": 50, "settled_orders": 1}
    assert me.star_balance == 50
    assert await _status_of(db, "order-1") == STATUS_PAID

    entries = await _entries(db, me)
    assert [(e.entry_type, e.reference_id, e.amount) for e in entries] == [
        (ENTRY_PURCHASE, "order-1", 50)
    ]


@pytest.mark.asyncio
async def test_repeated_reconcile_credits_only_once(db, make_user, toss):
    """정리를 두 번 돌려도 지급은 한 번. (앱을 연달아 껐다 켜는 경우)"""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me)
    toss({"order-1": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-1"})})

    await payments_service.reconcile_pending_orders(me, db)
    second = await payments_service.reconcile_pending_orders(me, db)

    # 두 번째에는 미확정 주문이 남아 있지 않다.
    assert second == {"star_balance": 50, "credited_stars": 0, "settled_orders": 0}
    assert me.star_balance == 50
    assert len(await _entries(db, me)) == 1


@pytest.mark.asyncio
async def test_already_granted_order_is_closed_without_double_credit(db, make_user, toss):
    """원장에 이미 지급이 있으면 주문만 닫고 스타는 더 주지 않는다.

    승인과 정리가 나란히 들어와 승인이 원장을 먼저 넣은 상황이다. 멱등키가 없으면
    여기서 스타가 두 배로 들어간다.
    """
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me)
    await star_ledger.record(
        db, me, entry_type=ENTRY_PURCHASE, reference_id="order-1", amount=50
    )
    await db.commit()
    toss({"order-1": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-1"})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result["credited_stars"] == 0
    assert result["settled_orders"] == 1
    assert me.star_balance == 50
    assert len(await _entries(db, me)) == 1
    assert await _status_of(db, "order-1") == STATUS_PAID


@pytest.mark.asyncio
async def test_amount_mismatch_is_left_for_a_human(db, make_user, toss):
    """결제된 금액이 주문 금액과 다르면 자동 지급하지 않고 미확정으로 남긴다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, amount=5500)
    toss({"order-1": (200, {"status": "DONE", "totalAmount": 1100, "paymentKey": "pk-1"})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result["credited_stars"] == 0
    assert me.star_balance == 0
    assert await _status_of(db, "order-1") == STATUS_PENDING
    assert await _entries(db, me) == []


# --- 실패·이탈한 경우 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_canceled_order_is_closed_as_failed(db, make_user, toss):
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me)
    toss({"order-1": (200, {"status": "CANCELED", "totalAmount": 5500})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result == {"star_balance": 0, "credited_stars": 0, "settled_orders": 1}
    assert await _status_of(db, "order-1") == STATUS_FAILED
    assert await _entries(db, me) == []


@pytest.mark.asyncio
async def test_abandoned_order_without_payment_is_closed(db, make_user, toss):
    """결제창까지 가지 못하고 버려진 오래된 주문은 닫는다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, age=timedelta(hours=2))
    toss({"order-1": (404, {"code": "NOT_FOUND_PAYMENT"})})

    await payments_service.reconcile_pending_orders(me, db)

    assert await _status_of(db, "order-1") == STATUS_FAILED


@pytest.mark.asyncio
async def test_fresh_order_without_payment_is_left_alone(db, make_user, toss):
    """방금 만든 주문은 아직 결제 중일 수 있다. 결제 기록이 없어도 닫지 않는다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, age=timedelta(seconds=30))
    toss({"order-1": (404, {"code": "NOT_FOUND_PAYMENT"})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result["settled_orders"] == 0
    assert await _status_of(db, "order-1") == STATUS_PENDING


@pytest.mark.asyncio
async def test_in_progress_order_is_left_alone(db, make_user, toss):
    """결제 진행 중인 주문을 정리가 가로채면 안 된다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, age=timedelta(hours=1))
    toss({"order-1": (200, {"status": "IN_PROGRESS", "totalAmount": 5500})})

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result["settled_orders"] == 0
    assert await _status_of(db, "order-1") == STATUS_PENDING


# --- 물어보지 못한 경우 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_network_failure_leaves_order_untouched(db, make_user, toss):
    """토스에 닿지 못하면 아무 판단도 하지 않는다 — 다음 실행 때 다시 본다."""
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, age=timedelta(hours=2))
    toss({}, offline=True)

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result == {"star_balance": 0, "credited_stars": 0, "settled_orders": 0}
    assert await _status_of(db, "order-1") == STATUS_PENDING


@pytest.mark.asyncio
async def test_no_toss_key_is_a_no_op(db, make_user, monkeypatch):
    """결제 키가 없는 환경(개발·테스트)에서도 예외 없이 지나간다."""
    monkeypatch.setattr(payments_service.settings, "toss_secret_key", "")
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me, age=timedelta(hours=2))

    result = await payments_service.reconcile_pending_orders(me, db)

    assert result == {"star_balance": 0, "credited_stars": 0, "settled_orders": 0}
    assert await _status_of(db, "order-1") == STATUS_PENDING


# --- 사용자 격리 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_users_orders_are_not_touched(db, make_user, toss):
    me = await make_user(kakao_id="me", star_balance=0)
    other = await make_user(kakao_id="other", star_balance=0)
    await _pending_order(db, me, order_id="mine")
    await _pending_order(db, other, order_id="theirs")
    toss(
        {
            "mine": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-1"}),
            "theirs": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-2"}),
        }
    )

    await payments_service.reconcile_pending_orders(me, db)

    assert me.star_balance == 50
    assert other.star_balance == 0
    assert await _status_of(db, "theirs") == STATUS_PENDING


# --- HTTP 경로 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_endpoint_requires_auth(client):
    resp = await client.post("/payments/reconcile")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reconcile_endpoint_returns_recovered_balance(
    client, db, make_user, auth_header, toss
):
    me = await make_user(kakao_id="me", star_balance=0)
    await _pending_order(db, me)
    toss({"order-1": (200, {"status": "DONE", "totalAmount": 5500, "paymentKey": "pk-1"})})

    resp = await client.post("/payments/reconcile", headers=auth_header(me))

    assert resp.status_code == 200
    assert resp.json() == {
        "star_balance": 50,
        "credited_stars": 50,
        "settled_orders": 1,
    }
