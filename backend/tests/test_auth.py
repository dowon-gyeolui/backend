"""인증 — 토큰 발급/검증, 만료 거부, 미인증 차단."""

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_token_roundtrip():
    token = create_access_token(42)
    assert decode_access_token(token) == 42


def test_expired_token_is_rejected():
    expired = jwt.encode(
        {"sub": "42", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    assert decode_access_token(expired) is None


def test_token_signed_with_other_key_is_rejected():
    forged = jwt.encode(
        {"sub": "42", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "attacker-key",
        algorithm=ALGORITHM,
    )
    assert decode_access_token(forged) is None


def test_token_without_subject_is_rejected():
    bad = jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    assert decode_access_token(bad) is None


def test_password_hash_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h)
    assert not verify_password("wrong-pw", h)


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    """DEBUG=false 에서는 Authorization 없이 보호 엔드포인트에 접근할 수 없다."""
    res = await client.get("/users/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_dev_user_header_does_not_work_when_debug_off(client, make_user):
    """X-Dev-User-Id 우회는 DEBUG=true 일 때만 열려야 한다."""
    user = await make_user()
    res = await client.get("/users/me", headers={"X-Dev-User-Id": str(user.id)})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_request_returns_own_profile(client, make_user, auth_header):
    user = await make_user(nickname="본인")
    res = await client.get("/users/me", headers=auth_header(user))
    assert res.status_code == 200
    assert res.json()["id"] == user.id


@pytest.mark.asyncio
async def test_token_of_deleted_user_is_rejected(client, db, make_user, auth_header):
    user = await make_user()
    header = auth_header(user)
    await db.delete(user)
    await db.commit()

    res = await client.get("/users/me", headers=header)
    assert res.status_code == 401
