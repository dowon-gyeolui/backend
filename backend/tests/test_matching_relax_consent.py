"""선호 조건 완화는 사용자가 동의했을 때만 한다 (OI-MATCH-003, T-E11).

확정 문구: "조건 위반 추천 금지. '조건에 맞는 사용자 없음' 안내 + 조건 완화 동의 팝업
(기다리기 / 조건 완화하기)".

여기서 지키는 것 두 가지:
1. 동의(`relax=true`) 없이는 **선호 조건을 벗어난 카드가 만들어지지 않는다.**
2. "기다리기"(= 동의 없이 그냥 두는 것)를 고르면 카드도, 차감도 남지 않는다.
"""

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.card_unlock import CardUnlock
from app.services.matching import (
    STAR_COST_PER_CARD,
    get_today_card,
    unlock_extra,
)


async def _card_count(db, user) -> int:
    rows = await db.execute(
        select(func.count(CardUnlock.id)).where(CardUnlock.user_id == user.id)
    )
    return int(rows.scalar_one())


@pytest.fixture
def make_picky(make_user):
    """조건이 까다로운 사용자 — 지역/키 어느 쪽으로도 후보와 어긋난다."""

    async def _make(**kwargs):
        defaults = dict(
            kakao_id="picky",
            gender="male",
            birth_date=date(1993, 1, 1),
            pref_age_min=25,
            pref_age_max=30,
            pref_region="서울",
            pref_height_min=170,
        )
        defaults.update(kwargs)
        return await make_user(**defaults)

    return _make


@pytest.mark.asyncio
async def test_today_card_is_not_issued_without_consent(db, make_picky, make_user):
    """조건 밖 후보만 있으면 오늘의 카드는 발급되지 않고 완화 동의를 물어본다."""
    me = await make_picky()
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)

    card, relax_available = await get_today_card(me, db)

    assert card is None
    assert relax_available is True
    assert await _card_count(db, me) == 0


@pytest.mark.asyncio
async def test_today_card_is_issued_after_consent(db, make_picky, make_user):
    """'조건 완화하기'를 누른 경우(relax=True)에만 완화 후보가 배정된다."""
    me = await make_picky()
    far = await make_user(
        kakao_id="far", gender="female", region="부산", height_cm=150
    )

    card, relax_available = await get_today_card(me, db, relax=True)

    assert card is not None and card.user_id == far.id
    assert relax_available is False
    assert await _card_count(db, me) == 1


@pytest.mark.asyncio
async def test_candidate_inside_preferences_needs_no_consent(
    db, make_picky, make_user
):
    """조건에 맞는 상대가 있으면 동의를 묻지 않고 그대로 발급한다(회귀 방어)."""
    me = await make_picky()
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)
    fit = await make_user(
        kakao_id="fit",
        gender="female",
        region="서울",
        height_cm=172,
        birth_date=date(1998, 5, 5),
    )

    card, relax_available = await get_today_card(me, db)

    assert card is not None and card.user_id == fit.id
    assert relax_available is False


@pytest.mark.asyncio
async def test_no_candidate_at_all_does_not_ask_for_consent(db, make_picky):
    """후보 자체가 없으면 완화해도 소용없다 — 팝업 대신 '아직 인연 없음'이다."""
    me = await make_picky()

    card, relax_available = await get_today_card(me, db)

    assert card is None
    assert relax_available is False


@pytest.mark.asyncio
async def test_waiting_creates_no_card_even_on_repeated_calls(
    db, make_picky, make_user
):
    """'기다리기'는 아무것도 하지 않는 선택이다 — 화면을 다시 열어도 카드가 생기지 않는다."""
    me = await make_picky()
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)

    for _ in range(3):
        card, relax_available = await get_today_card(me, db)
        assert card is None and relax_available is True

    assert await _card_count(db, me) == 0


@pytest.mark.asyncio
async def test_unlock_without_consent_charges_nothing(db, make_picky, make_user):
    """추가 열람도 동의 전에는 409 로 막고, 별을 쓰지 않는다."""
    me = await make_picky(star_balance=100)
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)

    with pytest.raises(HTTPException) as exc:
        await unlock_extra(me, db)

    assert exc.value.status_code == 409
    assert me.star_balance == 100
    assert await _card_count(db, me) == 0


@pytest.mark.asyncio
async def test_unlock_after_consent_issues_card_and_charges(
    db, make_picky, make_user
):
    me = await make_picky(star_balance=100)
    far = await make_user(
        kakao_id="far", gender="female", region="부산", height_cm=150
    )

    card = await unlock_extra(me, db, relax=True)

    assert card.user_id == far.id
    assert me.star_balance == 100 - STAR_COST_PER_CARD
    assert await _card_count(db, me) == 1


@pytest.mark.asyncio
async def test_today_endpoint_reports_relax_available(
    client, auth_header, make_picky, make_user
):
    me = await make_picky()
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)

    resp = await client.get("/matches/today", headers=auth_header(me))
    assert resp.status_code == 200
    body = resp.json()
    assert body["card"] is None
    assert body["relax_available"] is True

    consented = await client.get(
        "/matches/today", params={"relax": "true"}, headers=auth_header(me)
    )
    assert consented.status_code == 200
    assert consented.json()["card"] is not None
    assert consented.json()["relax_available"] is False


@pytest.mark.asyncio
async def test_unlock_endpoint_asks_for_consent_with_409(
    client, auth_header, make_picky, make_user
):
    me = await make_picky(star_balance=100)
    await make_user(kakao_id="far", gender="female", region="부산", height_cm=150)

    resp = await client.post("/matches/unlock", headers=auth_header(me))
    assert resp.status_code == 409
    assert "조건" in resp.json()["detail"]

    consented = await client.post(
        "/matches/unlock", params={"relax": "true"}, headers=auth_header(me)
    )
    assert consented.status_code == 200
    assert consented.json()["star_balance"] == 100 - STAR_COST_PER_CARD
