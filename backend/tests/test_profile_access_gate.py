"""프로필 상세·궁합 리포트는 카드를 열람(언락)한 상대에게만 열려야 한다.

보안 감사(2026-08-18)가 발견한 IDOR: GET /users/{id}/public-profile 과
GET /compatibility/report/{id} 가 차단 여부만 확인하고 언락 여부를 확인하지
않아, 로그인만 하면 순차 정수 id 를 돌며 전 회원의 개인정보(직업·종교·사주·
MBTI 등)를 무제한으로 긁어올 수 있었다. GET /recommendations/pair/{id} 는
이미 has_unlocked 를 정확히 검사하므로 그 패턴을 따라 맞췄다.
"""

import pytest

from app.models.card_unlock import KIND_DAILY, CardUnlock


@pytest.mark.asyncio
async def test_public_profile_requires_unlock(client, db, make_user, auth_header):
    me = await make_user(kakao_id="me", gender="male")
    stranger = await make_user(kakao_id="stranger", gender="female")

    resp = await client.get(
        f"/users/{stranger.id}/public-profile", headers=auth_header(me)
    )
    assert resp.status_code == 402


@pytest.mark.asyncio
async def test_public_profile_allowed_after_unlock(client, db, make_user, auth_header):
    me = await make_user(kakao_id="me2", gender="male")
    match = await make_user(kakao_id="match2", gender="female")
    db.add(CardUnlock(user_id=me.id, candidate_id=match.id, kind=KIND_DAILY))
    await db.commit()

    resp = await client.get(
        f"/users/{match.id}/public-profile", headers=auth_header(me)
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_compatibility_report_requires_unlock(client, db, make_user, auth_header):
    me = await make_user(kakao_id="me3", gender="male")
    stranger = await make_user(kakao_id="stranger3", gender="female")

    resp = await client.get(
        f"/compatibility/report/{stranger.id}", headers=auth_header(me)
    )
    assert resp.status_code == 402
