"""차단 관계가 프로필·궁합 조회에도 적용되는지 회귀 테스트 (T-B09).

DECISIONS OI-BLOCK-001 은 차단을 **양방향** 으로 본다 — 내가 차단했든 상대가 나를
차단했든 서로 보이지 않아야 한다. 매칭 후보 필터와 채팅은 이미 지키고 있었지만
`/users/{id}/public-profile` 과 `/compatibility/report/{id}` 는 빠져 있었다.
"""

import pytest

from app.models.block import UserBlock
from app.models.card_unlock import KIND_DAILY, CardUnlock


def _paths(peer_id: int) -> list[str]:
    return [f"/users/{peer_id}/public-profile", f"/compatibility/report/{peer_id}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path_index", [0, 1])
async def test_blocked_by_me_is_forbidden(
    client, db, make_user, auth_header, path_index
):
    me = await make_user(kakao_id="viewer", gender="male")
    peer = await make_user(kakao_id="blocked_peer", gender="female")
    db.add(UserBlock(blocker_id=me.id, blocked_id=peer.id))
    await db.commit()

    res = await client.get(_paths(peer.id)[path_index], headers=auth_header(me))
    assert res.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path_index", [0, 1])
async def test_peer_who_blocked_me_is_forbidden(
    client, db, make_user, auth_header, path_index
):
    me = await make_user(kakao_id="viewer2", gender="male")
    peer = await make_user(kakao_id="blocker_peer", gender="female")
    db.add(UserBlock(blocker_id=peer.id, blocked_id=me.id))
    await db.commit()

    res = await client.get(_paths(peer.id)[path_index], headers=auth_header(me))
    assert res.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path_index", [0, 1])
async def test_unblocked_and_unlocked_peer_is_visible(
    client, db, make_user, auth_header, path_index
):
    """차단이 없고 카드를 열람(언락)한 상대면 200 — 검사가 과하게 막지 않는지 확인.

    언락 검사는 이 테스트가 아니라 H-1 IDOR 수정(test_profile_access_gate.py)에서
    추가됐다. 여기서는 그 검사를 통과한 뒤에도 차단 검사가 정상 동작하는지만 본다.
    """
    me = await make_user(kakao_id="viewer3", gender="male")
    peer = await make_user(kakao_id="normal_peer", gender="female")
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    res = await client.get(_paths(peer.id)[path_index], headers=auth_header(me))
    assert res.status_code == 200
