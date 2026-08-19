"""회원 상태(비활성·차단)가 매칭 밖의 경로에도 걸리는지 회귀 테스트 (T-E10).

T-E03 이 만든 `users.status` 는 매칭 후보 풀에만 적용돼 있었다. 정지된 회원이
여전히 로그인하고, 채팅을 보내고, 이미 열람된 카드의 공개 프로필에 노출됐다.

여기서 고정하는 것은 두 가지다.
1. 정지된 회원 **본인**은 토큰을 받지도, 이미 가진 토큰으로 쓰지도 못한다.
2. 정지된 회원은 **남에게** 보이지 않는다 — 공개 프로필·궁합·채팅 전송.

그리고 그만큼 중요한 세 번째: 정상 회원은 아무것도 달라지지 않는다.
"""

import pytest

from app.core.security import hash_password
from app.models.card_unlock import KIND_DAILY, CardUnlock
from app.models.user import STATUS_ACTIVE, STATUS_BLOCKED, STATUS_INACTIVE
from app.services.account_status import ACCOUNT_STATUS_HEADER

_SUSPENDED = [STATUS_INACTIVE, STATUS_BLOCKED]

# 인증 의존성(get_current_user)을 그대로 쓰는 보호된 엔드포인트들. 한 곳만 확인하면
# "그 라우터만 고쳤다"는 착각을 낳아서, 성격이 다른 것으로 셋을 고른다.
_PROTECTED = ["/users/me", "/users/me/interview", "/chat/threads"]


# --- 1. 본인 -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _SUSPENDED)
@pytest.mark.parametrize("path", _PROTECTED)
async def test_suspended_token_cannot_use_protected_endpoints(
    client, db, make_user, auth_header, status, path
):
    user = await make_user(kakao_id=f"self_{status}_{path}", status=status)

    res = await client.get(path, headers=auth_header(user))

    assert res.status_code == 403
    # 클라이언트가 "권한 없음"·"재인증 필요"와 구분할 수 있어야 화면을 다르게 띄운다.
    assert res.headers.get(ACCOUNT_STATUS_HEADER) == status


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PROTECTED)
async def test_active_member_is_unaffected(client, make_user, auth_header, path):
    """회귀 확인 — 정상 회원의 경로는 그대로다."""
    user = await make_user(kakao_id=f"active_{path}", status=STATUS_ACTIVE)

    res = await client.get(path, headers=auth_header(user))

    assert res.status_code == 200
    assert ACCOUNT_STATUS_HEADER not in res.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _SUSPENDED)
async def test_suspended_member_cannot_log_in(client, db, make_user, status):
    """아이디/비밀번호 로그인 — 비밀번호가 맞아도 토큰을 주지 않는다."""
    user = await make_user(
        kakao_id=f"login_{status}",
        username=f"user_{status}",
        password_hash=hash_password("Passw0rd!23"),
        status=status,
    )
    assert user.id is not None

    res = await client.post(
        "/auth/login",
        json={"username": f"user_{status}", "password": "Passw0rd!23"},
    )

    assert res.status_code == 403
    assert "token" not in res.json()


@pytest.mark.asyncio
async def test_active_member_can_still_log_in(client, make_user):
    """회귀 확인 — 상태 검사가 정상 로그인을 막지 않는다."""
    await make_user(
        kakao_id="login_active",
        username="user_active",
        password_hash=hash_password("Passw0rd!23"),
        status=STATUS_ACTIVE,
    )

    res = await client.post(
        "/auth/login", json={"username": "user_active", "password": "Passw0rd!23"}
    )

    assert res.status_code == 200
    assert res.json()["token"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _SUSPENDED)
async def test_suspended_member_cannot_exchange_app_login_code(
    client, make_user, status
):
    """앱 딥링크 코드 교환 — 코드 유효기간(2분) 사이에 정지되면 여기서 막힌다."""
    from app.services.app_login_code import issue_login_code

    user = await make_user(kakao_id=f"exchange_{status}", status=status)
    verifier = "v" * 43
    challenge = _challenge_of(verifier)
    code = await issue_login_code(user.id, False, challenge)

    res = await client.post(
        "/auth/app/exchange", json={"code": code, "code_verifier": verifier}
    )

    assert res.status_code == 403
    assert "token" not in res.json()


def _challenge_of(code_verifier: str) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# --- 2. 상대 -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _SUSPENDED)
@pytest.mark.parametrize(
    "path", ["/users/{peer}/public-profile", "/compatibility/report/{peer}"]
)
async def test_suspended_peer_is_not_visible(
    client, db, make_user, auth_header, status, path
):
    """카드를 이미 열람한 상대라도 정지되면 보이지 않는다."""
    me = await make_user(kakao_id=f"viewer_{status}_{path}", gender="male")
    peer = await make_user(kakao_id=f"peer_{status}_{path}", gender="female", status=status)
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    res = await client.get(path.format(peer=peer.id), headers=auth_header(me))

    assert res.status_code == 403
    # 남의 계정 상태를 캐는 수단이 되지 않도록, 사유는 응답에 나오지 않는다.
    assert status not in res.text


@pytest.mark.asyncio
async def test_profile_hidden_peer_is_not_visible_but_report_is(
    client, db, make_user, auth_header
):
    """노출 중단은 프로필만 내린다 — 이미 이어진 인연의 궁합까지 끊지는 않는다."""
    me = await make_user(kakao_id="viewer_hidden", gender="male")
    peer = await make_user(kakao_id="peer_hidden", gender="female", profile_hidden=True)
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    profile = await client.get(
        f"/users/{peer.id}/public-profile", headers=auth_header(me)
    )
    report = await client.get(
        f"/compatibility/report/{peer.id}", headers=auth_header(me)
    )

    assert profile.status_code == 403
    assert report.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _SUSPENDED)
async def test_cannot_send_message_to_suspended_peer(
    client, db, make_user, auth_header, status
):
    me = await make_user(kakao_id=f"sender_{status}", gender="male")
    peer = await make_user(kakao_id=f"receiver_{status}", gender="female", status=status)
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    res = await client.post(
        f"/chat/with/{peer.id}/messages", json={"content": "안녕하세요"},
        headers=auth_header(me),
    )

    assert res.status_code == 403


@pytest.mark.asyncio
async def test_can_still_send_message_to_active_peer(
    client, db, make_user, auth_header
):
    """회귀 확인 — 정상 상대와의 채팅은 그대로다."""
    me = await make_user(kakao_id="sender_active", gender="male")
    peer = await make_user(kakao_id="receiver_active", gender="female")
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    res = await client.post(
        f"/chat/with/{peer.id}/messages", json={"content": "안녕하세요"},
        headers=auth_header(me),
    )

    assert res.status_code == 201
