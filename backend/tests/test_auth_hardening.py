"""무인증 우회 차단 · 기동 시 SECRET_KEY 검증 · 로그인 브루트포스 제한 (T-H06).

strix(자율 침투테스트, 2026-08-18)가 HIGH(CVSS 7.7)로 재확인한 결함을 고정한다.
`DEBUG=true` 가 기본값이던 시절에는 인증 헤더 없는 `GET /users/me` 가 200 을 주고
(가짜 `dev_1` 사용자), 예제 시크릿으로 서명한 위조 JWT 가 그대로 통과했다.
"""

import subprocess
import sys

import pytest

from app.core.security import hash_password
from app.main import _PLACEHOLDER_SECRET_KEYS, assert_secret_key_configured
from app.models.card_unlock import KIND_DAILY, CardUnlock

# --- 개발용 무인증 우회 -----------------------------------------------------------
#
# 우회가 열리려면 세 가지가 모두 필요하다: DEBUG · ALLOW_DEV_AUTH · X-Dev-User-Id 헤더.
# 아래 네 테스트가 "하나라도 빠지면 401" 을 각 축마다 고정한다.


@pytest.mark.asyncio
async def test_dev_auth_closed_when_debug_off(client, monkeypatch):
    """운영 기본값(DEBUG=false) — ALLOW_DEV_AUTH 만 켜도 열리지 않는다."""
    from app.config import settings

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "allow_dev_auth", True)

    res = await client.get("/users/me", headers={"X-Dev-User-Id": "1"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_dev_auth_closed_when_only_debug_on(client, monkeypatch):
    """DEBUG 만으로는 열리지 않는다 — 진단 라우트를 열려고 켠 DEBUG 에 인증 우회가
    딸려 열리는 것이 원래 사고의 경로였다."""
    from app.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "allow_dev_auth", False)

    res = await client.get("/users/me", headers={"X-Dev-User-Id": "1"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_dev_auth_needs_explicit_header(client, monkeypatch):
    """헤더가 없으면 401. 예전에는 조용히 `dev_1` 계정을 만들어 200 을 줬다."""
    from app.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "allow_dev_auth", True)

    res = await client.get("/users/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_dev_auth_opens_with_both_flags_and_header(client, monkeypatch):
    """세 조건이 다 갖춰지면 개발 편의 기능은 그대로 동작한다."""
    from app.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "allow_dev_auth", True)

    res = await client.get("/users/me", headers={"X-Dev-User-Id": "7"})
    assert res.status_code == 200


# --- 기동 시 SECRET_KEY 검증 -------------------------------------------------------


@pytest.mark.parametrize("placeholder", sorted(_PLACEHOLDER_SECRET_KEYS))
def test_placeholder_secret_key_refuses_startup(monkeypatch, placeholder):
    from app.config import settings

    monkeypatch.setattr(settings, "secret_key", placeholder)
    with pytest.raises(RuntimeError):
        assert_secret_key_configured()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_secret_key_refuses_startup(monkeypatch, blank):
    from app.config import settings

    monkeypatch.setattr(settings, "secret_key", blank)
    with pytest.raises(RuntimeError):
        assert_secret_key_configured()


def test_real_secret_key_passes():
    """conftest 가 넣은 정상 키로는 통과한다(검증이 항상 죽는 게 아님을 고정)."""
    assert_secret_key_configured()


def test_import_of_app_main_fails_with_placeholder_secret(tmp_path):
    """검증 함수가 실제로 **기동 경로에서 호출되는지** 확인한다.

    함수만 있고 부르는 곳이 없으면 위 단위 테스트는 전부 통과하면서도 서버는
    취약한 채로 뜬다. 그래서 별도 프로세스에서 진짜로 import 해 본다.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/t.db",
        "SECRET_KEY": "dev-secret-key-change-in-production",
        "DEBUG": "false",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode != 0, "예제 시크릿으로도 앱이 그냥 떴다"
    assert "SECRET_KEY" in proc.stderr


# --- 로그인 브루트포스 제한 --------------------------------------------------------


_PASSWORD = "correct-horse-battery"


async def _make_login_user(make_user, username="brute-target"):
    return await make_user(
        kakao_id=f"kakao-{username}",
        username=username,
        password_hash=hash_password(_PASSWORD),
    )


@pytest.mark.asyncio
async def test_repeated_failures_lock_out_the_username(client, make_user, monkeypatch):
    monkeypatch.setattr("app.routers.auth._LOGIN_FAIL_DAILY_LIMIT_PER_USERNAME", 3)
    user = await _make_login_user(make_user)

    for _ in range(3):
        res = await client.post(
            "/auth/login", json={"username": user.username, "password": "nope"}
        )
        assert res.status_code == 401

    res = await client.post(
        "/auth/login", json={"username": user.username, "password": "nope"}
    )
    assert res.status_code == 429


@pytest.mark.asyncio
async def test_correct_password_is_refused_after_lockout(
    client, make_user, monkeypatch
):
    """상한을 넘긴 뒤에는 비밀번호를 맞혀도 통과하지 못한다.

    제한을 비밀번호 검증 **뒤에** 걸면 여기서 200 이 나가고 브루트포스는 성공한다.
    """
    monkeypatch.setattr("app.routers.auth._LOGIN_FAIL_DAILY_LIMIT_PER_USERNAME", 2)
    user = await _make_login_user(make_user, "brute-target-2")

    for _ in range(2):
        await client.post(
            "/auth/login", json={"username": user.username, "password": "nope"}
        )

    res = await client.post(
        "/auth/login", json={"username": user.username, "password": _PASSWORD}
    )
    assert res.status_code == 429


@pytest.mark.asyncio
async def test_successful_logins_are_not_counted(client, make_user, monkeypatch):
    """실패만 센다 — 자주 로그인한다고 잠기면 안 된다."""
    monkeypatch.setattr("app.routers.auth._LOGIN_FAIL_DAILY_LIMIT_PER_USERNAME", 2)
    user = await _make_login_user(make_user, "frequent-user")

    for _ in range(5):
        res = await client.post(
            "/auth/login", json={"username": user.username, "password": _PASSWORD}
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_credential_stuffing_from_one_ip_is_capped(
    client, make_user, monkeypatch
):
    """아이디를 바꿔 가며 훑어도 출처 IP 기준 상한에 걸린다."""
    monkeypatch.setattr("app.routers.auth._LOGIN_FAIL_DAILY_LIMIT_PER_IP", 3)
    headers = {"X-Forwarded-For": "203.0.113.9"}

    for i in range(3):
        res = await client.post(
            "/auth/login",
            json={"username": f"victim-{i}", "password": "nope"},
            headers=headers,
        )
        assert res.status_code == 401

    res = await client.post(
        "/auth/login",
        json={"username": "victim-fresh", "password": "nope"},
        headers=headers,
    )
    assert res.status_code == 429

    # 다른 출처는 영향을 받지 않는다.
    res = await client.post(
        "/auth/login",
        json={"username": "victim-fresh", "password": "nope"},
        headers={"X-Forwarded-For": "198.51.100.4"},
    )
    assert res.status_code == 401


# --- 스크레이핑 상한 (T-H01 언락 게이트의 두 번째 방어선) ----------------------------


@pytest.mark.asyncio
async def test_public_profile_view_is_rate_limited(
    client, db, make_user, auth_header, monkeypatch
):
    monkeypatch.setattr("app.routers.users._PUBLIC_PROFILE_DAILY_LIMIT", 2)
    me = await make_user(kakao_id="scraper", gender="male")
    peer = await make_user(kakao_id="scraped", gender="female")
    db.add(CardUnlock(user_id=me.id, candidate_id=peer.id, kind=KIND_DAILY))
    await db.commit()

    for _ in range(2):
        res = await client.get(
            f"/users/{peer.id}/public-profile", headers=auth_header(me)
        )
        assert res.status_code == 200

    res = await client.get(f"/users/{peer.id}/public-profile", headers=auth_header(me))
    assert res.status_code == 429
