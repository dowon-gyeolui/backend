"""앱 로그인 1회용 코드 — 딥링크로 토큰이 새지 않는지, 코드가 재사용되지 않는지.

배경: 커스텀 스킴(com.melobe.app://)은 OS 가 소유권을 검증하지 않아 악성 앱이
리다이렉트를 가로챌 수 있다. 그래서 딥링크에는 코드만 싣고, 토큰은 verifier 를 아는
앱만 HTTPS 로 교환해 간다(T-H04).
"""

import base64
import hashlib
import secrets

import pytest

from app.core.security import decode_access_token
from app.services.app_login_code import issue_login_code, redeem_login_code


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


@pytest.mark.asyncio
async def test_code_roundtrip():
    verifier, challenge = _pkce_pair()
    code = await issue_login_code(7, True, challenge)

    assert code not in ("", "7")
    assert await redeem_login_code(code, verifier) == (7, True)


@pytest.mark.asyncio
async def test_code_is_single_use():
    """가로챈 코드가 나중에 다시 쓰이면 안 된다."""
    verifier, challenge = _pkce_pair()
    code = await issue_login_code(7, False, challenge)

    assert await redeem_login_code(code, verifier) == (7, False)
    assert await redeem_login_code(code, verifier) is None


@pytest.mark.asyncio
async def test_code_without_matching_verifier_is_rejected():
    """딥링크를 가로챈 앱은 코드를 얻어도 verifier 를 모른다."""
    _, challenge = _pkce_pair()
    other_verifier, _ = _pkce_pair()
    code = await issue_login_code(7, False, challenge)

    assert await redeem_login_code(code, other_verifier) is None


@pytest.mark.asyncio
async def test_unknown_code_is_rejected():
    verifier, _ = _pkce_pair()
    assert await redeem_login_code("nonexistent-code", verifier) is None


@pytest.mark.asyncio
async def test_app_login_requires_code_challenge(client):
    res = await client.get("/auth/kakao", params={"platform": "app"})
    assert res.status_code == 400

    res = await client.get(
        "/auth/kakao", params={"platform": "app", "code_challenge": "too-short"}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_app_login_passes_challenge_through_state(client):
    _, challenge = _pkce_pair()
    res = await client.get(
        "/auth/kakao", params={"platform": "app", "code_challenge": challenge}
    )

    assert res.status_code == 302
    assert f"state=app.{challenge}" in res.headers["location"]


@pytest.mark.asyncio
async def test_app_callback_redirects_with_code_not_token(client, make_user, monkeypatch):
    """딥링크 URL 에 JWT 가 실리면 안 된다 — 코드만 실린다."""
    user = await make_user(kakao_id="kakao-deeplink")
    _, challenge = _pkce_pair()
    _patch_kakao(monkeypatch, user)

    res = await client.get(
        "/auth/kakao/callback",
        params={"code": "kakao-auth-code", "state": f"app.{challenge}"},
        follow_redirects=False,
    )

    location = res.headers["location"]
    assert location.startswith("com.melobe.app://auth/callback?code=")
    assert "token=" not in location


@pytest.mark.asyncio
async def test_exchange_endpoint_returns_token_once(client, make_user, monkeypatch):
    user = await make_user(kakao_id="kakao-exchange")
    verifier, challenge = _pkce_pair()
    _patch_kakao(monkeypatch, user)

    res = await client.get(
        "/auth/kakao/callback",
        params={"code": "kakao-auth-code", "state": f"app.{challenge}"},
        follow_redirects=False,
    )
    login_code = res.headers["location"].split("code=", 1)[1]

    res = await client.post(
        "/auth/app/exchange", json={"code": login_code, "code_verifier": verifier}
    )
    assert res.status_code == 200
    body = res.json()
    assert decode_access_token(body["token"]) == user.id
    assert body["is_new"] is False

    # 같은 코드를 다시 내밀면 거부된다.
    res = await client.post(
        "/auth/app/exchange", json={"code": login_code, "code_verifier": verifier}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_exchange_rejects_wrong_verifier(client, make_user, monkeypatch):
    user = await make_user(kakao_id="kakao-wrong-verifier")
    _, challenge = _pkce_pair()
    other_verifier, _ = _pkce_pair()
    _patch_kakao(monkeypatch, user)

    res = await client.get(
        "/auth/kakao/callback",
        params={"code": "kakao-auth-code", "state": f"app.{challenge}"},
        follow_redirects=False,
    )
    login_code = res.headers["location"].split("code=", 1)[1]

    res = await client.post(
        "/auth/app/exchange",
        json={"code": login_code, "code_verifier": other_verifier},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_web_callback_still_carries_token(client, make_user, monkeypatch):
    """웹은 https 리다이렉트라 기존 흐름을 그대로 둔다."""
    user = await make_user(kakao_id="kakao-web")
    _patch_kakao(monkeypatch, user)

    res = await client.get(
        "/auth/kakao/callback", params={"code": "kakao-auth-code"}, follow_redirects=False
    )

    location = res.headers["location"]
    assert "token=" in location
    assert not location.startswith("com.melobe.app://")


def _patch_kakao(monkeypatch, user):
    """카카오 서버 왕복 세 단계를 이 사용자로 고정한다."""

    async def _token(_code):
        return "kakao-access-token"

    async def _profile(_token):
        return {"id": user.kakao_id}

    async def _upsert(_profile, _db):
        return user

    monkeypatch.setattr("app.routers.auth.exchange_code_for_token", _token)
    monkeypatch.setattr("app.routers.auth.fetch_kakao_profile", _profile)
    monkeypatch.setattr("app.routers.auth.upsert_kakao_user", _upsert)
