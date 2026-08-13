"""매칭 하드필터 — 위반 후보가 후보군에 0건이어야 한다.

QA 기능정의서 ADM-MATCH-002 체크포인트("하드필터 위반 포함 0건") 및
OI-MATCH-001 확정(Hard = 성별 / 차단 / 계정 정지 / 탈퇴)에 대응.
"""

from datetime import date

import pytest

from app.models.block import UserBlock
from app.services.compatibility import _candidate_pool


@pytest.mark.asyncio
async def test_self_is_never_a_candidate(db, make_user):
    me = await make_user(kakao_id="me", gender="male")
    pool = await _candidate_pool(me, db)
    assert me.id not in [c.id for c in pool]


@pytest.mark.asyncio
async def test_same_gender_is_excluded(db, make_user):
    me = await make_user(kakao_id="me", gender="male")
    same = await make_user(kakao_id="same", gender="male")
    opposite = await make_user(kakao_id="opp", gender="female")

    ids = [c.id for c in await _candidate_pool(me, db)]
    assert same.id not in ids
    assert opposite.id in ids


@pytest.mark.asyncio
async def test_user_i_blocked_is_excluded(db, make_user):
    me = await make_user(kakao_id="me", gender="male")
    target = await make_user(kakao_id="blocked", gender="female")
    db.add(UserBlock(blocker_id=me.id, blocked_id=target.id))
    await db.commit()

    assert target.id not in [c.id for c in await _candidate_pool(me, db)]


@pytest.mark.asyncio
async def test_user_who_blocked_me_is_excluded(db, make_user):
    """OI-BLOCK-001 확정: 차단은 양방향으로 매칭에서 제외된다."""
    me = await make_user(kakao_id="me", gender="male")
    blocker = await make_user(kakao_id="blocker", gender="female")
    db.add(UserBlock(blocker_id=blocker.id, blocked_id=me.id))
    await db.commit()

    assert blocker.id not in [c.id for c in await _candidate_pool(me, db)]


@pytest.mark.asyncio
async def test_incomplete_profile_is_excluded(db, make_user):
    """생년월일 없음 / 사진 없음은 후보가 될 수 없다 (사주 계산·노출 불가)."""
    me = await make_user(kakao_id="me", gender="male")
    no_birth = await make_user(kakao_id="nb", gender="female", birth_date=None)
    no_photo = await make_user(kakao_id="np", gender="female", photo_url=None)
    ok = await make_user(kakao_id="ok", gender="female")

    ids = [c.id for c in await _candidate_pool(me, db)]
    assert no_birth.id not in ids
    assert no_photo.id not in ids
    assert ok.id in ids


@pytest.mark.asyncio
async def test_hard_filters_hold_together(db, make_user):
    """여러 위반 사유가 섞여 있어도 통과하는 후보만 남는다."""
    me = await make_user(kakao_id="me", gender="male", birth_date=date(1993, 1, 1))
    valid = await make_user(kakao_id="valid", gender="female")
    await make_user(kakao_id="male2", gender="male")
    await make_user(kakao_id="nobirth", gender="female", birth_date=None)
    blocked = await make_user(kakao_id="blk", gender="female")
    db.add(UserBlock(blocker_id=me.id, blocked_id=blocked.id))
    await db.commit()

    ids = [c.id for c in await _candidate_pool(me, db)]
    assert ids == [valid.id]
