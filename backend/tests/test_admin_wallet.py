"""관리자 재화 원장 (T-E07) — QA 체크포인트를 고정한다.

ADM-WALLET-001 이 요구하는 세 가지가 이 파일의 뼈대다.

1. **현재 잔액 직접 수정 금지** — 잔액을 목표값으로 받는 경로가 라우팅에도 스키마에도
   없어야 한다(`test_no_endpoint_can_set_balance_directly`).
2. **잔액 불일치 검증 기능**(완료 조건) — 잔액과 원장 합계가 어긋난 회원을 목록과
   상세 양쪽에서 찾을 수 있어야 하고, 합계가 맞아도 **중간이 끊긴** 원장을 정상이라고
   답하면 안 된다.
3. **연속 클릭 중복 지급 방지**(완료 조건) — `test_adjust_twice_credits_once`.

목록(SQL 집계)과 상세(파이썬 누적)가 서로 다른 방식으로 같은 사실을 계산하므로,
`test_list_and_detail_agree_on_mismatch` 가 둘의 일치를 고정한다. 어긋나면
"목록엔 불일치, 상세엔 정상"이 나오고 그 거짓말은 아무도 모르는 채 남는다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.admin_rbac import ROLE_VIEWER
from app.core.security import create_access_token
from app.models.star_ledger import (
    ENTRY_ADMIN_GRANT,
    ENTRY_ADMIN_REVOKE,
    ENTRY_CARD_UNLOCK,
    ENTRY_PURCHASE,
    ENTRY_TEST_TOPUP,
    StarLedger,
)
from app.schemas.admin_wallet import MAX_ADJUST_AMOUNT
from app.services import admin_wallet

# 관리자 픽스처는 T-E01 테스트의 것을 그대로 쓴다(T-E03·T-E04·T-E06 과 같은 이유).
from tests.test_admin_rbac import _header, audit_logs, make_admin  # noqa: F401

_REASON = "고객센터 보상 #1234"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def make_member(make_user):
    """회원을 만든다. `kakao_id` 를 번호로 준다.

    conftest 의 `make_user` 는 기본 `kakao_id` 를 `id(kwargs)` 로 만드는데, 파이썬은
    수거된 dict 의 주소를 재사용하므로 한 테스트에서 같은 모양의 인자로 두 명을 만들면
    Unique 제약에 걸린다. 이 화면의 테스트는 회원을 여러 명 만드는 것이 기본이라
    여기서 번호를 붙인다.
    """
    seq = 0

    async def _make(**kwargs):
        nonlocal seq
        seq += 1
        kwargs.setdefault("kakao_id", f"wallet_test_{seq}")
        return await make_user(**kwargs)

    return _make


async def _entry(
    db,
    user,
    *,
    entry_type: str,
    reference_id: str,
    amount: int,
    balance_after: int | None = None,
    created_at: datetime | None = None,
    sync_balance: bool = True,
) -> StarLedger:
    """원장 한 줄을 직접 넣는다.

    `sync_balance=False` 는 **일부러 어긋난 상태**를 만들기 위한 것이다. 불일치 검증이
    실제로 무언가를 잡는지는 어긋난 데이터 없이는 확인할 수 없다.
    """
    after = user.star_balance + amount if balance_after is None else balance_after
    entry = StarLedger(
        user_id=user.id,
        entry_type=entry_type,
        reference_id=reference_id,
        amount=amount,
        balance_after=after,
        created_at=created_at or _utcnow(),
    )
    db.add(entry)
    if sync_balance:
        user.star_balance = after
    await db.commit()
    await db.refresh(entry)
    return entry


async def _ledger_rows(db, user_id: int, entry_type: str) -> list[StarLedger]:
    rows = await db.execute(
        select(StarLedger).where(
            StarLedger.user_id == user_id, StarLedger.entry_type == entry_type
        )
    )
    return list(rows.scalars())


def _adjust_body(**kwargs) -> dict:
    body = {
        "direction": "grant",
        "amount": 5,
        "reason": _REASON,
        "request_id": "req-abcdef12",
    }
    body.update(kwargs)
    return body


# --- 잔액 직접 수정 금지 ---------------------------------------------------------


def test_no_endpoint_can_set_balance_directly():
    """`/admin/wallet` 하위의 쓰기 경로는 조정 하나뿐이고, 목표 잔액을 받지 않는다.

    "잔액 직접 수정 금지"를 주석이 아니라 **라우팅과 스키마의 모양**으로 고정한다.
    나중에 누가 편의를 위해 잔액 필드를 열면 여기서 걸린다.
    """
    from app.main import app
    from app.schemas.admin_wallet import AdjustRequest

    writes = {
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin/wallet")
        for method in getattr(route, "methods", set())
        if method not in {"GET", "HEAD", "OPTIONS"}
    }
    assert writes == {("POST", "/admin/wallet/{user_id}/adjust")}
    assert set(AdjustRequest.model_fields) == {
        "direction",
        "amount",
        "reason",
        "request_id",
    }


def test_entry_type_dictionary_covers_every_type():
    """원장에 쓰이는 유형이 하나라도 사전에 빠지면 화면이 그 줄을 코드값으로 보여준다."""
    known = {meta.code for meta in admin_wallet.ENTRY_TYPES}
    assert known == {
        ENTRY_PURCHASE,
        ENTRY_CARD_UNLOCK,
        ENTRY_TEST_TOPUP,
        ENTRY_ADMIN_GRANT,
        ENTRY_ADMIN_REVOKE,
    }


# --- 목록 ----------------------------------------------------------------------


async def test_list_shows_balance_and_ledger_sum(client, db, make_member, make_admin):
    admin = await make_admin()
    user = await make_member(nickname="정상회원")
    await _entry(db, user, entry_type=ENTRY_PURCHASE, reference_id="ord-1", amount=10)

    res = await client.get("/admin/wallet", headers=_header(admin))
    assert res.status_code == 200
    body = res.json()
    row = next(item for item in body["items"] if item["member"]["id"] == user.id)
    assert row["member"]["star_balance"] == 10
    assert row["ledger_sum"] == 10
    assert row["entry_count"] == 1
    assert row["diff"] == 0
    assert row["balance_matches"] is True
    assert body["mismatch_total"] == 0


async def test_member_without_ledger_is_not_a_mismatch(client, make_member, make_admin):
    """원장이 한 줄도 없는 신규 회원(잔액 0)이 불일치로 잡히면 목록이 못 쓰게 된다."""
    admin = await make_admin()
    user = await make_member(nickname="신규")

    res = await client.get("/admin/wallet", headers=_header(admin))
    row = next(item for item in res.json()["items"] if item["member"]["id"] == user.id)
    assert row["entry_count"] == 0
    assert row["ledger_sum"] == 0
    assert row["balance_matches"] is True


async def test_mismatch_filter_and_total(client, db, make_member, make_admin):
    admin = await make_admin()
    ok = await make_member(nickname="정상")
    await _entry(db, ok, entry_type=ENTRY_PURCHASE, reference_id="ord-ok", amount=10)
    broken = await make_member(nickname="어긋남")
    # 원장은 10인데 잔액만 30 — 잔액을 원장 밖에서 건드린 상태.
    await _entry(
        db, broken, entry_type=ENTRY_PURCHASE, reference_id="ord-bad", amount=10
    )
    broken.star_balance = 30
    await db.commit()

    res = await client.get(
        "/admin/wallet", params={"mismatch": "true"}, headers=_header(admin)
    )
    body = res.json()
    assert body["total"] == 1
    assert body["mismatch_total"] == 1
    assert [item["member"]["id"] for item in body["items"]] == [broken.id]
    assert body["items"][0]["diff"] == 20
    assert body["items"][0]["balance_matches"] is False


async def test_mismatch_rows_come_first(client, db, make_member, make_admin):
    """불일치가 아래로 밀리면 페이지를 넘겨야 보이고, 그러면 아무도 안 본다."""
    admin = await make_admin()
    for i in range(3):
        u = await make_member(nickname=f"정상{i}")
        await _entry(
            db, u, entry_type=ENTRY_PURCHASE, reference_id=f"ord-{i}", amount=5
        )
    broken = await make_member(nickname="어긋남")
    broken.star_balance = 7
    await db.commit()

    res = await client.get("/admin/wallet", headers=_header(admin))
    items = res.json()["items"]
    assert items[0]["member"]["id"] == broken.id
    assert items[0]["balance_matches"] is False


async def test_list_search_by_nickname_and_id(client, make_member, make_admin):
    admin = await make_admin()
    target = await make_member(nickname="찾을사람")
    await make_member(nickname="다른사람")

    by_name = await client.get(
        "/admin/wallet", params={"q": "찾을"}, headers=_header(admin)
    )
    assert [i["member"]["id"] for i in by_name.json()["items"]] == [target.id]

    by_id = await client.get(
        "/admin/wallet", params={"q": str(target.id)}, headers=_header(admin)
    )
    assert [i["member"]["id"] for i in by_id.json()["items"]] == [target.id]


async def test_mismatch_total_ignores_filters(client, db, make_member, make_admin):
    """검색을 걸어도 불일치 건수는 전체 기준이어야 한다."""
    admin = await make_admin()
    broken = await make_member(nickname="어긋남")
    broken.star_balance = 3
    await db.commit()
    await make_member(nickname="정상")

    res = await client.get(
        "/admin/wallet", params={"q": "정상"}, headers=_header(admin)
    )
    body = res.json()
    assert [i["member"]["id"] for i in body["items"]] != [broken.id]
    assert body["mismatch_total"] == 1


# --- 상세 · 검증 ----------------------------------------------------------------


async def test_detail_reports_before_and_after_balance(client, db, make_member, make_admin):
    """전잔액/후잔액 연속성 — 원장을 눈으로 따라갈 수 있어야 한다."""
    admin = await make_admin()
    user = await make_member()
    now = _utcnow()
    await _entry(
        db,
        user,
        entry_type=ENTRY_PURCHASE,
        reference_id="ord-1",
        amount=10,
        created_at=now - timedelta(minutes=2),
    )
    await _entry(
        db,
        user,
        entry_type=ENTRY_CARD_UNLOCK,
        reference_id="3:9",
        amount=-3,
        created_at=now - timedelta(minutes=1),
    )

    res = await client.get(f"/admin/wallet/{user.id}", headers=_header(admin))
    body = res.json()
    # 최신순으로 내려온다.
    assert [e["amount"] for e in body["entries"]] == [-3, 10]
    assert [e["balance_before"] for e in body["entries"]] == [10, 0]
    assert [e["balance_after"] for e in body["entries"]] == [7, 10]
    assert body["audit"]["ledger_sum"] == 7
    assert body["audit"]["star_balance"] == 7
    assert body["audit"]["balance_matches"] is True
    assert body["audit"]["continuity_breaks"] == []
    assert body["max_revoke"] == 7
    assert body["max_amount"] == MAX_ADJUST_AMOUNT


async def test_detail_detects_balance_mismatch(client, db, make_member, make_admin):
    admin = await make_admin()
    user = await make_member()
    await _entry(db, user, entry_type=ENTRY_PURCHASE, reference_id="ord-1", amount=10)
    user.star_balance = 25
    await db.commit()

    res = await client.get(f"/admin/wallet/{user.id}", headers=_header(admin))
    audit = res.json()["audit"]
    assert audit["ledger_sum"] == 10
    assert audit["star_balance"] == 25
    assert audit["diff"] == 15
    assert audit["balance_matches"] is False


async def test_detail_detects_continuity_break_even_when_sum_matches(
    client, db, make_member, make_admin
):
    """합계가 맞는데 중간이 끊긴 원장을 정상이라고 답하면 안 된다.

    반대 부호의 누락 두 건이 있으면 합계는 우연히 맞는다. 검증을 합계 하나로 줄이면
    바로 이 경우를 놓친다 — 그래서 연속성을 따로 본다.
    """
    admin = await make_admin()
    user = await make_member()
    now = _utcnow()
    # 잔액 0 → 10 (정상)
    await _entry(
        db,
        user,
        entry_type=ENTRY_PURCHASE,
        reference_id="ord-1",
        amount=10,
        created_at=now - timedelta(minutes=3),
    )
    # 전잔액이 10 이어야 하는데 30 이라고 말한다 (기록되지 않은 +20 이 있었다)
    await _entry(
        db,
        user,
        entry_type=ENTRY_CARD_UNLOCK,
        reference_id="1:2",
        amount=-3,
        balance_after=27,
        created_at=now - timedelta(minutes=2),
        sync_balance=False,
    )
    # 다시 앞뒤가 맞는 자리로 돌아온다 → 합계는 7 로 잔액과 같아진다
    await _entry(
        db,
        user,
        entry_type=ENTRY_CARD_UNLOCK,
        reference_id="1:3",
        amount=0,
        balance_after=7,
        created_at=now - timedelta(minutes=1),
        sync_balance=False,
    )
    user.star_balance = 7
    await db.commit()

    res = await client.get(f"/admin/wallet/{user.id}", headers=_header(admin))
    audit = res.json()["audit"]
    assert audit["balance_matches"] is True  # 합계는 맞는다
    assert len(audit["continuity_breaks"]) == 2  # 과정은 끊겼다
    first = audit["continuity_breaks"][0]
    assert first["expected_before"] == 10
    assert first["actual_before"] == 30


async def test_list_and_detail_agree_on_mismatch(client, db, make_member, make_admin):
    """목록(SQL 집계)과 상세(파이썬 누적)가 같은 답을 해야 한다."""
    admin = await make_admin()
    users = []
    for i, (amount, balance) in enumerate([(10, 10), (10, 25), (-5, -5), (0, 0)]):
        user = await make_member(nickname=f"회원{i}")
        if amount:
            await _entry(
                db,
                user,
                entry_type=ENTRY_PURCHASE,
                reference_id=f"ord-{i}",
                amount=amount,
                balance_after=amount if amount > 0 else 0,
                sync_balance=False,
            )
        user.star_balance = max(balance, 0)
        await db.commit()
        users.append(user)

    listed = await client.get(
        "/admin/wallet", params={"size": 100}, headers=_header(admin)
    )
    by_id = {i["member"]["id"]: i for i in listed.json()["items"]}
    for user in users:
        detail = await client.get(f"/admin/wallet/{user.id}", headers=_header(admin))
        audit = detail.json()["audit"]
        assert by_id[user.id]["ledger_sum"] == audit["ledger_sum"]
        assert by_id[user.id]["diff"] == audit["diff"]
        assert by_id[user.id]["balance_matches"] == audit["balance_matches"]


async def test_detail_paginates_without_breaking_audit(client, db, make_member, make_admin):
    """검증은 전체 원장으로, 표시만 잘라야 한다. 페이지마다 답이 달라지면 안 된다."""
    admin = await make_admin()
    user = await make_member()
    now = _utcnow()
    for i in range(5):
        await _entry(
            db,
            user,
            entry_type=ENTRY_PURCHASE,
            reference_id=f"ord-{i}",
            amount=2,
            created_at=now - timedelta(minutes=10 - i),
        )

    first = await client.get(
        f"/admin/wallet/{user.id}", params={"size": 2}, headers=_header(admin)
    )
    second = await client.get(
        f"/admin/wallet/{user.id}",
        params={"size": 2, "page": 2},
        headers=_header(admin),
    )
    assert first.json()["total"] == 5
    assert len(first.json()["entries"]) == 2
    assert len(second.json()["entries"]) == 2
    assert first.json()["audit"] == second.json()["audit"]
    assert first.json()["audit"]["continuity_breaks"] == []


async def test_detail_404_for_unknown_member(client, make_admin):
    admin = await make_admin()
    res = await client.get("/admin/wallet/9999", headers=_header(admin))
    assert res.status_code == 404


# --- 운영 지급 · 회수 -----------------------------------------------------------


async def test_grant_credits_and_records_ledger(
    client, db, make_member, make_admin, audit_logs
):
    admin = await make_admin()
    user = await make_member()

    res = await client.post(
        f"/admin/wallet/{user.id}/adjust",
        json=_adjust_body(amount=7),
        headers=_header(admin),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert body["detail"]["member"]["star_balance"] == 7
    assert body["detail"]["audit"]["balance_matches"] is True

    rows = await _ledger_rows(db, user.id, ENTRY_ADMIN_GRANT)
    assert len(rows) == 1
    assert rows[0].amount == 7
    assert rows[0].balance_after == 7
    assert rows[0].reference_id == "req-abcdef12"

    logs = await audit_logs()
    assert [(log.menu, log.action) for log in logs] == [("재화", "지급")]
    assert logs[0].reason == _REASON
    assert logs[0].after["star_balance"] == 7


async def test_adjust_twice_credits_once(client, db, make_member, make_admin, audit_logs):
    """완료 조건: 버튼을 연속으로 눌러도 지급은 한 번뿐이다.

    같은 폼에서 나가는 요청은 `request_id` 가 같고, `(reference_id, entry_type)` Unique
    가 두 번째부터를 막는다. 두 번째 응답은 오류가 아니라 `applied=false` 다.
    """
    admin = await make_admin()
    user = await make_member()
    body = _adjust_body(amount=5, request_id="dup-req-0001")

    results = []
    for _ in range(4):
        res = await client.post(
            f"/admin/wallet/{user.id}/adjust", json=body, headers=_header(admin)
        )
        assert res.status_code == 200
        results.append(res.json())

    assert [r["applied"] for r in results] == [True, False, False, False]
    rows = await _ledger_rows(db, user.id, ENTRY_ADMIN_GRANT)
    assert len(rows) == 1
    await db.refresh(user)
    assert user.star_balance == 5
    assert results[-1]["detail"]["member"]["star_balance"] == 5

    # 반영되지 않은 클릭도 감사 로그에는 남는다(B-2 — 눌렀다는 사실이 기록 대상).
    logs = await audit_logs()
    assert len(logs) == 4
    assert [log.after["applied"] for log in logs] == [True, False, False, False]


async def test_different_request_id_grants_again(client, db, make_member, make_admin):
    """멱등키는 '같은 요청'을 막는 것이지 '두 번째 지급'을 막는 것이 아니다."""
    admin = await make_admin()
    user = await make_member()
    for request_id in ("first-req-1", "second-req-2"):
        res = await client.post(
            f"/admin/wallet/{user.id}/adjust",
            json=_adjust_body(amount=3, request_id=request_id),
            headers=_header(admin),
        )
        assert res.json()["applied"] is True
    await db.refresh(user)
    assert user.star_balance == 6


async def test_revoke_deducts(client, db, make_member, make_admin, audit_logs):
    admin = await make_admin()
    user = await make_member()
    await _entry(db, user, entry_type=ENTRY_PURCHASE, reference_id="ord-1", amount=10)

    res = await client.post(
        f"/admin/wallet/{user.id}/adjust",
        json=_adjust_body(direction="revoke", amount=4, request_id="rev-req-001"),
        headers=_header(admin),
    )
    assert res.json()["applied"] is True
    rows = await _ledger_rows(db, user.id, ENTRY_ADMIN_REVOKE)
    assert [r.amount for r in rows] == [-4]
    assert rows[0].balance_after == 6
    await db.refresh(user)
    assert user.star_balance == 6
    logs = await audit_logs()
    assert [log.action for log in logs] == ["회수"]


async def test_revoke_more_than_balance_is_rejected(client, db, make_member, make_admin):
    """음수 잔액을 만드는 회수는 없다 — users·star_ledger 양쪽 CHECK 가 그렇게 되어 있다."""
    admin = await make_admin()
    user = await make_member()
    await _entry(db, user, entry_type=ENTRY_PURCHASE, reference_id="ord-1", amount=3)

    res = await client.post(
        f"/admin/wallet/{user.id}/adjust",
        json=_adjust_body(direction="revoke", amount=5, request_id="rev-req-002"),
        headers=_header(admin),
    )
    assert res.status_code == 400
    assert "잔액" in res.json()["detail"]
    await db.refresh(user)
    assert user.star_balance == 3
    assert await _ledger_rows(db, user.id, ENTRY_ADMIN_REVOKE) == []


@pytest.mark.parametrize(
    "body",
    [
        _adjust_body(amount=0),
        _adjust_body(amount=-5),
        _adjust_body(amount=MAX_ADJUST_AMOUNT + 1),
        _adjust_body(reason="x"),
        _adjust_body(request_id="short"),
        _adjust_body(direction="set"),
        {"direction": "grant", "amount": 5, "reason": _REASON},  # request_id 누락
    ],
)
async def test_adjust_rejects_bad_input(client, make_member, make_admin, body):
    """부호 실수·오타·사유 누락이 조용히 통과하면 안 된다."""
    admin = await make_admin()
    user = await make_member()
    res = await client.post(
        f"/admin/wallet/{user.id}/adjust", json=body, headers=_header(admin)
    )
    assert res.status_code == 422


async def test_adjust_404_for_unknown_member(client, make_admin):
    admin = await make_admin()
    res = await client.post(
        "/admin/wallet/9999/adjust", json=_adjust_body(), headers=_header(admin)
    )
    assert res.status_code == 404


# --- 권한 ----------------------------------------------------------------------


async def test_viewer_can_read_but_not_adjust(client, db, make_member, make_admin):
    """URL 을 직접 두드려도 같은 답이 나와야 한다(QA 체크포인트: RBAC)."""
    viewer = await make_admin(email="viewer-wallet@example.com", role=ROLE_VIEWER)
    user = await make_member()

    assert (await client.get("/admin/wallet", headers=_header(viewer))).status_code == 200
    assert (
        await client.get(f"/admin/wallet/{user.id}", headers=_header(viewer))
    ).status_code == 200

    res = await client.post(
        f"/admin/wallet/{user.id}/adjust",
        json=_adjust_body(),
        headers=_header(viewer),
    )
    assert res.status_code == 403
    assert await _ledger_rows(db, user.id, ENTRY_ADMIN_GRANT) == []


async def test_app_user_token_cannot_open_wallet(client, make_member):
    """앱 사용자 토큰으로 관리자 재화 화면이 열리면 안 된다."""
    user = await make_member()
    res = await client.get(
        "/admin/wallet",
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )
    assert res.status_code == 401


async def test_wallet_requires_token(client, make_member):
    user = await make_member()
    assert (await client.get("/admin/wallet")).status_code == 401
    assert (
        await client.post(f"/admin/wallet/{user.id}/adjust", json=_adjust_body())
    ).status_code == 401
