"""관리자 결제 목록·상세 (T-E06) — QA 체크포인트를 고정한다.

ADM-PAY-001/002 가 요구하는 세 가지가 이 파일의 뼈대다.

1. **결제사가 원천이다(OI-PAY-001).** 관리자가 결제 성공 상태를 임의로 바꾸거나 금액을
   고칠 수 있는 경로가 없어야 하고, 지급 재처리도 결제사가 성공이라 답한 주문에만 통해야 한다.
2. **지급 이상 필터** — "결제 성공인데 재화 미지급"을 목록에서 찾을 수 있어야 한다.
3. **버튼 연속 클릭 시 중복 지급 0건**(완료 조건) — `test_regrant_twice_credits_once`.

`test_sql_and_python_issue_detection_agree` 도 그만큼 중요하다. 이상 판정이 SQL(필터)과
파이썬(배지) 두 벌이라, 어긋나면 "목록엔 이상인데 상세엔 정상"이 나온다. 두 경로를
맞대는 테스트가 없으면 그 거짓말은 아무도 모르는 채 남는다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.admin_rbac import ROLE_VIEWER
from app.core.security import create_access_token
from app.models.payment import STATUS_FAILED, STATUS_PAID, STATUS_PENDING, StarOrder
from app.models.star_ledger import ENTRY_PURCHASE, StarLedger
from app.schemas.admin_payment import ProviderPayment
from app.services import admin_payments
from app.services.admin_payments import (
    ISSUE_AMOUNT_MISMATCH,
    ISSUE_CODES,
    ISSUE_NOT_CREDITED,
    ISSUE_ORPHAN_CREDIT,
    ISSUE_STALE_PENDING,
    issues_for,
)

# 관리자 픽스처는 T-E01 테스트의 것을 그대로 쓴다(T-E03·T-E04 와 같은 이유).
from tests.test_admin_rbac import _header, audit_logs, make_admin  # noqa: F401

_REASON = "결제 문의 확인"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _order(
    db,
    user,
    *,
    order_id: str,
    status: str = STATUS_PAID,
    amount: int = 1100,
    stars: int = 10,
    product_id: str = "STAR-001",
    created_at: datetime | None = None,
    payment_key: str | None = None,
) -> StarOrder:
    order = StarOrder(
        user_id=user.id,
        order_id=order_id,
        product_id=product_id,
        amount=amount,
        star_amount=stars,
        status=status,
        payment_key=payment_key,
        created_at=created_at or _utcnow(),
        paid_at=_utcnow() if status == STATUS_PAID else None,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def _credit(db, user, order: StarOrder, *, stars: int | None = None) -> StarLedger:
    """이 주문에 대한 충전 원장을 직접 넣는다(승인이 정상적으로 끝난 상태)."""
    amount = order.star_amount if stars is None else stars
    entry = StarLedger(
        user_id=user.id,
        entry_type=ENTRY_PURCHASE,
        reference_id=order.order_id,
        amount=amount,
        balance_after=user.star_balance + amount,
    )
    user.star_balance += amount
    db.add(entry)
    await db.commit()
    return entry


def _provider(**kwargs) -> ProviderPayment:
    base = dict(
        available=True,
        reason=None,
        status="DONE",
        total_amount=1100,
        payment_key="toss-key",
        method="카드",
        approved_at="2026-08-19T12:00:00+09:00",
        amount_matches=True,
        success_matches=True,
    )
    base.update(kwargs)
    return ProviderPayment(**base)


def _stub_provider(monkeypatch, provider: ProviderPayment):
    """결제사 응답을 고정한다. 호출 횟수를 셀 수 있게 리스트를 돌려준다."""
    calls: list[str] = []

    async def _fake(order):
        calls.append(order.order_id)
        return provider

    monkeypatch.setattr(admin_payments, "fetch_provider_payment", _fake)
    return calls


async def _ledger_rows(db, order_id: str) -> list[StarLedger]:
    rows = await db.execute(
        select(StarLedger).where(
            StarLedger.reference_id == order_id,
            StarLedger.entry_type == ENTRY_PURCHASE,
        )
    )
    return list(rows.scalars())


# --- 권한 ----------------------------------------------------------------------


async def test_requires_admin_token(client, make_user):
    """앱 사용자 토큰으로는 열리지 않는다. 두 토큰의 id 공간이 겹치기 때문이다."""
    user = await make_user()
    assert (await client.get("/admin/payments")).status_code == 401

    res = await client.get(
        "/admin/payments",
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )
    assert res.status_code == 401


async def test_viewer_reads_but_cannot_regrant(client, make_user, make_admin, db):
    """UI 를 숨기는 것이 아니라 URL 을 직접 두드려도 막혀야 한다."""
    viewer = await make_admin(role=ROLE_VIEWER)
    user = await make_user()
    order = await _order(db, user, order_id="ord-viewer")
    headers = _header(viewer)

    assert (await client.get("/admin/payments", headers=headers)).status_code == 200
    assert (
        await client.get(f"/admin/payments/{order.order_id}", headers=headers)
    ).status_code == 200
    assert (
        await client.get(f"/admin/payments/{order.order_id}/provider", headers=headers)
    ).status_code == 200

    res = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": _REASON},
        headers=headers,
    )
    assert res.status_code == 403
    assert await _ledger_rows(db, order.order_id) == []


async def test_no_endpoint_can_change_status_or_amount(client):
    """결제 상태·금액을 바꾸는 경로가 **아예 없다**는 것을 스키마가 아니라 라우팅으로 고정한다.

    "금지"를 주석에만 적어 두면 다음 사람이 편의를 위해 PATCH 하나를 더한다.
    """
    from app.main import app

    paths = {
        (route.path, method)
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin/payments")
        for method in getattr(route, "methods", set())
    }
    writes = {(p, m) for p, m in paths if m not in {"GET", "HEAD", "OPTIONS"}}
    assert writes == {("/admin/payments/{order_id}/regrant", "POST")}


# --- 이상 판정 -----------------------------------------------------------------


async def _population(db, make_user):
    """이상 유형이 골고루 섞인 주문 무리. 여러 테스트가 같은 모양을 쓴다."""
    user = await make_user(kakao_id="payer", nickname="결제자")
    old = _utcnow() - timedelta(hours=3)

    normal = await _order(db, user, order_id="ord-normal")
    await _credit(db, user, normal)

    not_credited = await _order(db, user, order_id="ord-not-credited")

    orphan = await _order(db, user, order_id="ord-orphan", status=STATUS_PENDING)
    await _credit(db, user, orphan)

    mismatch = await _order(db, user, order_id="ord-mismatch")
    await _credit(db, user, mismatch, stars=3)

    stale = await _order(
        db, user, order_id="ord-stale", status=STATUS_PENDING, created_at=old
    )
    fresh = await _order(db, user, order_id="ord-fresh", status=STATUS_PENDING)
    failed = await _order(db, user, order_id="ord-failed", status=STATUS_FAILED)

    return {
        "user": user,
        "normal": normal,
        "not_credited": not_credited,
        "orphan": orphan,
        "mismatch": mismatch,
        "stale": stale,
        "fresh": fresh,
        "failed": failed,
    }


async def test_sql_and_python_issue_detection_agree(db, make_user):
    """필터(SQL)와 배지(파이썬)가 같은 주문을 고른다. 어긋나면 화면이 거짓말을 한다."""
    await _population(db, make_user)
    now = _utcnow()

    rows = (await db.execute(admin_payments._base_query())).all()
    assert rows

    for code in ISSUE_CODES:
        by_python = {
            order.order_id
            for order, ledger, _user in rows
            if code in issues_for(order, ledger, now=now)
        }
        selected = await db.execute(
            admin_payments._base_query().where(
                admin_payments.issue_condition(code, now=now)
            )
        )
        by_sql = {order.order_id for order, _l, _u in selected.all()}
        assert by_sql == by_python, code

    any_selected = await db.execute(
        admin_payments._base_query().where(admin_payments.any_issue_condition(now=now))
    )
    assert {o.order_id for o, _l, _u in any_selected.all()} == {
        order.order_id
        for order, ledger, _user in rows
        if issues_for(order, ledger, now=now)
    }


async def test_issue_codes_are_reported_together(db, make_user):
    """한 주문에 사유가 여러 개면 전부 나온다. 하나만 주면 나머지는 아무도 모른다."""
    people = await _population(db, make_user)
    row = await admin_payments.load_order(db, people["orphan"].order_id)
    order, ledger, _user = row
    assert issues_for(order, ledger, now=_utcnow()) == [ISSUE_ORPHAN_CREDIT]

    both = await _order(db, people["user"], order_id="ord-both", status=STATUS_PENDING)
    await _credit(db, people["user"], both, stars=1)
    order, ledger, _user = await admin_payments.load_order(db, both.order_id)
    assert issues_for(order, ledger, now=_utcnow()) == [
        ISSUE_ORPHAN_CREDIT,
        ISSUE_AMOUNT_MISMATCH,
    ]


async def test_fresh_pending_order_is_not_an_issue(db, make_user):
    """방금 만든 미확정 주문은 이상이 아니다 — 사용자가 지금 카드번호를 넣는 중일 수 있다."""
    people = await _population(db, make_user)
    order, ledger, _user = await admin_payments.load_order(db, people["fresh"].order_id)
    assert issues_for(order, ledger, now=_utcnow()) == []


# --- 목록 ----------------------------------------------------------------------


async def test_list_filters_not_credited_orders(client, db, make_user, make_admin):
    """지급 이상 필터 — "결제 성공 + 재화 미지급"만 골라 낸다."""
    admin = await make_admin()
    people = await _population(db, make_user)

    res = await client.get(
        f"/admin/payments?issue={ISSUE_NOT_CREDITED}", headers=_header(admin)
    )
    assert res.status_code == 200
    body = res.json()
    assert [item["order_id"] for item in body["items"]] == [
        people["not_credited"].order_id
    ]
    assert body["items"][0]["issues"] == [ISSUE_NOT_CREDITED]
    assert body["items"][0]["credited_stars"] is None


async def test_list_issue_counts_are_global_not_filtered(client, db, make_user, make_admin):
    """유형별 건수는 필터 이전 전체 기준이다. 필터를 옮길 때마다 0 이 되면 쓸모가 없다."""
    admin = await make_admin()
    await _population(db, make_user)

    res = await client.get(
        f"/admin/payments?issue={ISSUE_NOT_CREDITED}", headers=_header(admin)
    )
    counts = {row["code"]: row["count"] for row in res.json()["issue_counts"]}
    assert counts == {
        ISSUE_NOT_CREDITED: 1,
        ISSUE_ORPHAN_CREDIT: 1,
        ISSUE_AMOUNT_MISMATCH: 1,
        ISSUE_STALE_PENDING: 1,
    }
    assert {issue["code"] for issue in res.json()["issues"]} == set(ISSUE_CODES)


async def test_list_is_newest_first_and_filters_by_status_and_member(
    client, db, make_user, make_admin
):
    admin = await make_admin()
    people = await _population(db, make_user)
    other = await make_user(kakao_id="other", nickname="다른사람")
    await _order(db, other, order_id="ord-other", status=STATUS_PENDING)

    body = (await client.get("/admin/payments", headers=_header(admin))).json()
    created = [item["created_at"] for item in body["items"]]
    assert created == sorted(created, reverse=True)

    body = (
        await client.get(f"/admin/payments?status={STATUS_FAILED}", headers=_header(admin))
    ).json()
    assert [item["order_id"] for item in body["items"]] == [people["failed"].order_id]

    body = (
        await client.get(f"/admin/payments?member_id={other.id}", headers=_header(admin))
    ).json()
    assert [item["order_id"] for item in body["items"]] == ["ord-other"]


async def test_list_search_matches_order_id_nickname_and_member_id(
    client, db, make_user, make_admin
):
    admin = await make_admin()
    people = await _population(db, make_user)
    user = people["user"]

    for term, expected in (
        ("ord-stale", {people["stale"].order_id}),
        ("결제자", {o.order_id for o in await _all_orders(db, user.id)}),
        (str(user.id), {o.order_id for o in await _all_orders(db, user.id)}),
    ):
        body = (
            await client.get(f"/admin/payments?q={term}&size=100", headers=_header(admin))
        ).json()
        assert {item["order_id"] for item in body["items"]} == expected, term


async def _all_orders(db, user_id: int) -> list[StarOrder]:
    rows = await db.execute(select(StarOrder).where(StarOrder.user_id == user_id))
    return list(rows.scalars())


async def test_date_filter_uses_kst_calendar_days(client, db, make_user, make_admin):
    """관리자가 말하는 "8월 19일"은 한국 날짜다. UTC 로 읽으면 밤 9시 이후 결제가 사라진다."""
    admin = await make_admin()
    user = await make_user()
    # 2026-08-19 00:30 KST = 2026-08-18 15:30 UTC — UTC 기준으로는 전날이다.
    await _order(
        db,
        user,
        order_id="ord-kst-dawn",
        created_at=datetime(2026, 8, 18, 15, 30, tzinfo=timezone.utc),
    )
    # 2026-08-18 23:30 KST = 2026-08-18 14:30 UTC
    await _order(
        db,
        user,
        order_id="ord-kst-prev",
        created_at=datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc),
    )

    body = (
        await client.get(
            "/admin/payments?from=2026-08-19&to=2026-08-19", headers=_header(admin)
        )
    ).json()
    assert [item["order_id"] for item in body["items"]] == ["ord-kst-dawn"]


async def test_list_does_not_expose_masked_member_fields(client, db, make_user, make_admin):
    """결제 화면이 회원 상세의 마스킹을 우회하는 통로가 되면 안 된다(OI-MEM-004)."""
    from datetime import date

    admin = await make_admin()
    user = await make_user(
        kakao_id="kakao-9876543", username="minji01", birth_date=date(1995, 3, 20)
    )
    await _order(db, user, order_id="ord-pii")

    raw = (await client.get("/admin/payments", headers=_header(admin))).text
    assert "kakao-9876543" not in raw
    assert "minji01" not in raw
    assert "1995-03-20" not in raw


# --- 상세 ----------------------------------------------------------------------


async def test_detail_shows_ledger_and_regrant_availability(
    client, db, make_user, make_admin
):
    admin = await make_admin()
    people = await _population(db, make_user)

    body = (
        await client.get(
            f"/admin/payments/{people['not_credited'].order_id}", headers=_header(admin)
        )
    ).json()
    assert body["can_regrant"] is True
    assert body["regrant_blocker"] is None
    assert body["ledger"] == []
    assert body["order"]["issues"] == [ISSUE_NOT_CREDITED]

    body = (
        await client.get(
            f"/admin/payments/{people['normal'].order_id}", headers=_header(admin)
        )
    ).json()
    assert body["can_regrant"] is False
    assert body["regrant_blocker"]
    assert [entry["entry_type"] for entry in body["ledger"]] == [ENTRY_PURCHASE]
    assert body["order"]["issues"] == []


async def test_detail_404_for_unknown_order(client, make_admin):
    admin = await make_admin()
    res = await client.get("/admin/payments/nope", headers=_header(admin))
    assert res.status_code == 404


async def test_provider_lookup_reports_unavailable_without_key(
    client, db, make_user, make_admin
):
    """키가 없으면 "결제 없음"이 아니라 "물어보지 못했다"여야 한다.

    둘을 같은 값으로 보여주면 결제사 장애가 곧 "결제 안 됨"이 되어 판단이 뒤집힌다.
    """
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-nokey")

    body = (
        await client.get(
            f"/admin/payments/{order.order_id}/provider", headers=_header(admin)
        )
    ).json()
    assert body["available"] is False
    assert body["reason"]
    assert body["status"] is None


async def test_provider_lookup_maps_toss_answer(client, db, make_user, make_admin, monkeypatch):
    """결제사 답을 그대로 옮기고, 우리 주문과의 불일치를 함께 계산한다."""
    from app.config import settings
    from app.services import payments as payments_service

    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-toss", status=STATUS_PENDING)

    monkeypatch.setattr(settings, "toss_secret_key", "test-key")

    async def _fake_query(_client, order_id):
        assert order_id == order.order_id
        return {
            "status": "DONE",
            "totalAmount": 1100,
            "paymentKey": "toss-abc",
            "method": "카드",
            "approvedAt": "2026-08-19T12:00:00+09:00",
        }

    monkeypatch.setattr(payments_service, "_query_toss_order", _fake_query)

    body = (
        await client.get(
            f"/admin/payments/{order.order_id}/provider", headers=_header(admin)
        )
    ).json()
    assert body["available"] is True
    assert body["status"] == "DONE"
    assert body["amount_matches"] is True
    # 결제사는 성공인데 우리 주문은 미확정 — 이 불일치가 곧 재처리 대상이다.
    assert body["success_matches"] is False
    assert body["payment_key"] == "toss-abc"


# --- 지급 재처리 ---------------------------------------------------------------


async def test_regrant_twice_credits_once(client, db, make_user, make_admin, monkeypatch):
    """**완료 조건** — 버튼을 연속으로 눌러도 중복 지급 0건.

    두 번째 응답은 오류가 아니라 `granted=false` 다. 오류로 돌려주면 운영자가
    "안 됐나 보다" 하고 또 누른다.
    """
    admin = await make_admin()
    user = await make_user()
    before = user.star_balance
    order = await _order(db, user, order_id="ord-regrant")
    _stub_provider(monkeypatch, _provider())
    headers = _header(admin)

    first = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": _REASON},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["granted"] is True
    assert first.json()["credited_stars"] == 10

    for _ in range(4):
        again = await client.post(
            f"/admin/payments/{order.order_id}/regrant",
            json={"reason": _REASON},
            headers=headers,
        )
        assert again.status_code == 200
        assert again.json()["granted"] is False
        assert again.json()["credited_stars"] == 0

    rows = await _ledger_rows(db, order.order_id)
    assert len(rows) == 1
    assert rows[0].amount == 10
    await db.refresh(user)
    assert user.star_balance == before + 10
    assert first.json()["detail"]["can_regrant"] is False


async def test_regrant_requires_reason(client, db, make_user, make_admin, monkeypatch):
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-noreason")
    _stub_provider(monkeypatch, _provider())

    res = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": ""},
        headers=_header(admin),
    )
    assert res.status_code == 422
    assert await _ledger_rows(db, order.order_id) == []


async def test_regrant_refuses_when_provider_unreachable(
    client, db, make_user, make_admin, monkeypatch
):
    """물어보지 못했으면 아무것도 하지 않는다. 그러지 않으면 이 버튼은 무료 스타 발급기다."""
    admin = await make_admin()
    user = await make_user()
    before = user.star_balance
    order = await _order(db, user, order_id="ord-unreachable", status=STATUS_PENDING)
    _stub_provider(
        monkeypatch,
        _provider(available=False, reason="결제사에 연결하지 못했어요.", status=None),
    )

    res = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 503
    assert await _ledger_rows(db, order.order_id) == []
    await db.refresh(order)
    await db.refresh(user)
    assert order.status == STATUS_PENDING
    assert user.star_balance == before


@pytest.mark.parametrize("provider_status", ["CANCELED", "READY", "NO_PAYMENT"])
async def test_regrant_refuses_when_provider_is_not_done(
    client, db, make_user, make_admin, monkeypatch, provider_status
):
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id=f"ord-{provider_status}")
    _stub_provider(monkeypatch, _provider(status=provider_status))

    res = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 409
    assert await _ledger_rows(db, order.order_id) == []


async def test_regrant_refuses_on_amount_mismatch(
    client, db, make_user, make_admin, monkeypatch
):
    """금액이 다르면 지급하지 않는다. 금액을 맞추는 기능도 두지 않는다(ADM-PAY 금지 항목)."""
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-amount", amount=1100)
    _stub_provider(monkeypatch, _provider(total_amount=100))

    res = await client.post(
        f"/admin/payments/{order.order_id}/regrant",
        json={"reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 409
    assert await _ledger_rows(db, order.order_id) == []
    await db.refresh(order)
    assert order.amount == 1100


async def test_regrant_syncs_pending_order_confirmed_by_provider(
    client, db, make_user, make_admin, monkeypatch
):
    """결제사가 성공이라 하면 우리 상태를 그쪽에 맞춘다 — 관리자가 고른 값이 아니다.

    사용자가 앱으로 돌아오지 않으면 정리(`reconcile_pending_orders`)가 영영 돌지 않는다.
    이 경로가 아니면 "돈은 냈는데 스타가 없는" 주문을 아무도 고칠 수 없다.
    """
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-pending-done", status=STATUS_PENDING)
    _stub_provider(monkeypatch, _provider(payment_key="toss-xyz"))

    body = (
        await client.post(
            f"/admin/payments/{order.order_id}/regrant",
            json={"reason": _REASON},
            headers=_header(admin),
        )
    ).json()
    assert body["granted"] is True
    await db.refresh(order)
    assert order.status == STATUS_PAID
    assert order.payment_key == "toss-xyz"
    assert order.paid_at is not None
    assert body["detail"]["order"]["issues"] == []


async def test_regrant_does_not_ask_provider_when_already_credited(
    client, db, make_user, make_admin, monkeypatch
):
    """이미 지급된 주문은 결제사에 묻지도 않는다. 헛물음은 곧 바깥 호출 비용이다."""
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-already")
    await _credit(db, user, order)
    calls = _stub_provider(monkeypatch, _provider())

    body = (
        await client.post(
            f"/admin/payments/{order.order_id}/regrant",
            json={"reason": _REASON},
            headers=_header(admin),
        )
    ).json()
    assert body["granted"] is False
    assert calls == []
    assert len(await _ledger_rows(db, order.order_id)) == 1


async def test_regrant_records_audit_log_even_when_nothing_granted(
    client, db, make_user, make_admin, audit_logs, monkeypatch
):
    """금전 가치가 걸린 버튼은 "눌렀다"는 사실 자체가 기록 대상이다(B-2)."""
    admin = await make_admin()
    user = await make_user()
    order = await _order(db, user, order_id="ord-audit")
    _stub_provider(monkeypatch, _provider())
    headers = _header(admin)

    for _ in range(2):
        await client.post(
            f"/admin/payments/{order.order_id}/regrant",
            json={"reason": "고객센터 문의 #1234"},
            headers=headers,
        )

    logs = [entry for entry in await audit_logs() if entry.action == "지급재처리"]
    assert len(logs) == 2
    assert logs[0].menu == "결제"
    assert logs[0].admin_id == admin.id
    assert logs[0].target_id == order.order_id
    assert logs[0].reason == "고객센터 문의 #1234"
    assert logs[0].after["granted"] is True
    assert logs[0].after["credited_stars"] == 10
    assert logs[1].after["granted"] is False
