"""유휴 세션 만료와 민감 액션 재인증 (T-H05 / OI-AUTH-002).

토큰 탈취의 피해 범위를 시간으로 줄이는 두 겹의 방어를 검증한다.
  1. 유휴 만료 — 마지막 활동 + 2시간. 활동이 있으면 응답에 새 토큰이 실려 시계가 다시 감긴다.
  2. 민감 액션 재인증 — 탈퇴·결제 승인·자격증명 변경은 "최근에 직접 인증"했어야 통과한다.
     갱신은 `auth_time` 을 옮기지 않으므로, 토큰만 쥔 공격자는 이 문턱을 넘지 못한다.

토큰을 손으로 조립하는 이유: 시간을 앞으로 돌릴 방법이 없어, "2시간 논 세션의 토큰"을
그 시점에 발급됐을 모습 그대로 만든다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import settings
from app.core.deps import REAUTH_REQUIRED_HEADER, REFRESHED_TOKEN_HEADER
from app.core.security import (
    ALGORITHM,
    create_access_token,
    decode_token_claims,
    hash_password,
)


def _token(user_id: int, *, issued_ago: timedelta, auth_ago: timedelta | None = None) -> str:
    """`issued_ago` 전에 발급된 토큰. 유휴 만료(exp)는 발급 시점 기준으로 붙는다."""
    now = datetime.now(timezone.utc)
    issued_at = now - issued_ago
    auth_time = now - (auth_ago if auth_ago is not None else issued_ago)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "auth_time": int(auth_time.timestamp()),
        "exp": issued_at + timedelta(minutes=settings.idle_timeout_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 유휴 만료 ----------------------------------------------------------------


def test_issued_token_expires_after_idle_timeout_not_a_week():
    """발급 시 exp 는 절대 수명(7일)이 아니라 유휴 시간(2시간)이어야 한다."""
    payload = jwt.decode(
        create_access_token(42), settings.secret_key, algorithms=[ALGORITHM]
    )
    lifetime = payload["exp"] - payload["iat"]
    assert lifetime == pytest.approx(settings.idle_timeout_minutes * 60, abs=2)


@pytest.mark.asyncio
async def test_idle_token_is_rejected(client, make_user):
    """2시간 넘게 아무 요청도 없던 세션의 토큰은 더 이상 통하지 않는다."""
    user = await make_user()
    idle = timedelta(minutes=settings.idle_timeout_minutes + 1)

    res = await client.get("/users/me", headers=_header(_token(user.id, issued_ago=idle)))

    assert res.status_code == 401


@pytest.mark.asyncio
async def test_active_session_gets_a_refreshed_token(client, make_user):
    """활동이 있으면 새 토큰이 응답에 실려 유휴 시계가 다시 감긴다. 인증 시각은 그대로."""
    user = await make_user()
    auth_ago = timedelta(minutes=30)

    res = await client.get(
        "/users/me",
        headers=_header(_token(user.id, issued_ago=timedelta(minutes=10), auth_ago=auth_ago)),
    )

    assert res.status_code == 200
    refreshed = res.headers.get(REFRESHED_TOKEN_HEADER)
    assert refreshed is not None

    claims = decode_token_claims(refreshed)
    assert claims is not None
    assert claims.user_id == user.id
    # 갱신은 유휴 시계만 되감는다 — 로그인 시각(auth_time)까지 미루면 재인증이 무의미해진다.
    expected_auth = datetime.now(timezone.utc) - auth_ago
    assert abs((claims.auth_time - expected_auth).total_seconds()) < 5


@pytest.mark.asyncio
async def test_fresh_token_is_not_refreshed_every_request(client, make_user, auth_header):
    """방금 받은 토큰까지 매 요청 재발급하지는 않는다."""
    user = await make_user()

    res = await client.get("/users/me", headers=auth_header(user))

    assert res.status_code == 200
    assert REFRESHED_TOKEN_HEADER not in res.headers


@pytest.mark.asyncio
async def test_session_absolute_cap_is_enforced(client, make_user):
    """갱신을 반복해도 로그인 후 절대 수명을 넘긴 세션은 재로그인해야 한다."""
    user = await make_user()
    too_old = timedelta(minutes=settings.access_token_expire_minutes + 60)

    res = await client.get(
        "/users/me",
        headers=_header(_token(user.id, issued_ago=timedelta(minutes=1), auth_ago=too_old)),
    )

    assert res.status_code == 401


@pytest.mark.asyncio
async def test_legacy_token_without_iat_still_authenticates(client, make_user):
    """이 변경 이전에 나간 토큰(iat·auth_time 없음)으로도 로그인 상태가 유지된다."""
    user = await make_user()
    # 옛 토큰의 exp 는 "발급 시각 + 절대 수명"이었다. 30분 전에 로그인한 세션을 흉내낸다.
    legacy = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(timezone.utc)
            + timedelta(minutes=settings.access_token_expire_minutes - 30),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )

    res = await client.get("/users/me", headers=_header(legacy))

    assert res.status_code == 200
    # 유휴 만료가 붙은 새 토큰으로 즉시 갈아끼워진다.
    assert res.headers.get(REFRESHED_TOKEN_HEADER) is not None


# --- 민감 액션 재인증 ----------------------------------------------------------


def _stale_auth_header(user_id: int) -> dict[str, str]:
    """세션은 살아 있지만 마지막 인증이 재인증 창을 넘긴 상태."""
    return _header(
        _token(
            user_id,
            issued_ago=timedelta(minutes=1),
            auth_ago=timedelta(minutes=settings.reauth_window_minutes + 5),
        )
    )


@pytest.mark.asyncio
async def test_account_deletion_requires_recent_auth(client, make_user):
    user = await make_user()

    res = await client.delete("/users/me", headers=_stale_auth_header(user.id))

    assert res.status_code == 403
    assert res.headers.get(REAUTH_REQUIRED_HEADER) == "1"


@pytest.mark.asyncio
async def test_account_deletion_passes_with_recent_auth(client, make_user, auth_header):
    user = await make_user()

    res = await client.delete("/users/me", headers=auth_header(user))

    assert res.status_code == 204


@pytest.mark.asyncio
async def test_payment_confirm_requires_recent_auth(client, make_user):
    user = await make_user()
    body = {"payment_key": "pk_test", "order_id": "melobe_none", "amount": 1100}

    blocked = await client.post(
        "/payments/confirm", json=body, headers=_stale_auth_header(user.id)
    )

    assert blocked.status_code == 403
    assert blocked.headers.get(REAUTH_REQUIRED_HEADER) == "1"


@pytest.mark.asyncio
async def test_payment_confirm_passes_gate_with_recent_auth(client, make_user, auth_header):
    """최근 인증이면 재인증 게이트를 지나 실제 주문 확인 단계까지 간다(주문 없음 → 404)."""
    user = await make_user()
    body = {"payment_key": "pk_test", "order_id": "melobe_none", "amount": 1100}

    res = await client.post("/payments/confirm", json=body, headers=auth_header(user))

    assert res.status_code == 404


@pytest.mark.asyncio
async def test_credential_change_requires_recent_auth(client, make_user):
    """이미 비밀번호가 있는 계정의 자격증명 변경은 재인증 대상이다."""
    user = await make_user(username="olduser", password_hash=hash_password("old-password"))

    res = await client.post(
        "/users/me/credentials",
        json={"username": "newuser", "password": "new-password-1"},
        headers=_stale_auth_header(user.id),
    )

    assert res.status_code == 403
    assert res.headers.get(REAUTH_REQUIRED_HEADER) == "1"


@pytest.mark.asyncio
async def test_first_credential_setup_is_not_blocked(client, make_user):
    """온보딩의 최초 설정은 막지 않는다 — 아직 확인할 비밀번호 자체가 없다."""
    user = await make_user()

    res = await client.post(
        "/users/me/credentials",
        json={"username": "firsttime", "password": "first-password-1"},
        headers=_stale_auth_header(user.id),
    )

    assert res.status_code == 200


# --- 재인증 경로 (/auth/reauth) -------------------------------------------------


@pytest.mark.asyncio
async def test_reauth_unblocks_the_sensitive_action(client, make_user):
    user = await make_user(username="olduser", password_hash=hash_password("old-password"))
    stale = _stale_auth_header(user.id)

    reauth = await client.post("/auth/reauth", json={"password": "old-password"}, headers=stale)
    assert reauth.status_code == 200

    res = await client.post(
        "/users/me/credentials",
        json={"username": "newuser", "password": "new-password-1"},
        headers=_header(reauth.json()["token"]),
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_reauth_rejects_wrong_password(client, make_user, auth_header):
    user = await make_user(username="olduser", password_hash=hash_password("old-password"))

    res = await client.post(
        "/auth/reauth", json={"password": "wrong-password"}, headers=auth_header(user)
    )

    # 400 이어야 한다. 401 이면 클라이언트가 로그인이 풀린 줄 알고 토큰을 버린다.
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_reauth_tells_password_less_account_to_use_kakao(client, make_user, auth_header):
    user = await make_user()

    res = await client.post(
        "/auth/reauth", json={"password": "anything"}, headers=auth_header(user)
    )

    assert res.status_code == 409
    assert "카카오" in res.json()["detail"]
