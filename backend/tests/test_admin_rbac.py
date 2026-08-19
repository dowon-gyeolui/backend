"""관리자 RBAC (T-E01) — 권한별 접근과 감사 로그를 고정한다.

여기서 막고 싶은 사고는 네 가지다.

1. **Viewer 가 URL 을 직접 두드려 Super Admin 화면에 들어가는 것.** 관리자 앱이
   버튼을 숨기는 것은 통제가 아니다(QA 체크포인트: RBAC).
2. **앱 사용자 토큰과 관리자 토큰이 서로 통하는 것.** 두 테이블은 id 공간이 겹쳐,
   scope 검사가 빠지면 `users.id=1` 로 관리자 화면이 열린다.
3. **로그인 화면이 계정 열거 API 가 되는 것.** 없는 이메일과 틀린 비밀번호의 답이
   다르면 "이 사람이 관리자인지"를 밖에서 확인할 수 있다(ADM-AUTH-F004).
4. **부트스트랩 비밀번호 그대로 운영에 들어가는 것.** 비밀번호를 바꾸기 전에는
   어떤 화면도 열리지 않아야 한다(D-8).
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core.admin_rbac import (
    ADMIN_READ,
    ALL_PERMISSIONS,
    MEMBER_READ,
    MEMBER_UNMASK,
    MEMBER_WRITE,
    PERMISSIONS_BY_ROLE,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
    has_permission,
    permissions_for,
)
from app.core.security import (
    ALGORITHM,
    SCOPE_ADMIN,
    create_access_token,
    create_admin_access_token,
    hash_password,
)
from app.database import Base
from app.models.admin import AdminUser
from app.models.audit_log import AuditLog
from app.services import audit

_PASSWORD = "bootstrap-1234"


@pytest_asyncio.fixture
async def audit_logs(monkeypatch):
    """감사 로그를 별도 DB 로 받아 읽는다.

    `record_admin_action` 은 **호출자 세션을 쓰지 않는다**(기록 실패가 본 작업을 죽이지
    않게 하려는 설계). 그 경로를 그대로 태우되, 요청 세션과 커넥션을 공유하지 않도록
    전용 엔진을 준다 — in-memory SQLite 는 커넥션 하나를 공유하면 두 트랜잭션이 서로를
    덮어쓴다.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit, "AsyncSessionLocal", maker)

    async def _read() -> list[AuditLog]:
        async with maker() as session:
            result = await session.execute(select(AuditLog).order_by(AuditLog.id))
            return list(result.scalars())

    try:
        yield _read
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def make_admin(db):
    async def _make(**kwargs):
        defaults = dict(
            email=f"admin{id(kwargs)}@example.com",
            name="관리자",
            password_hash=hash_password(_PASSWORD),
            role=ROLE_SUPER_ADMIN,
            must_change_password=False,
        )
        defaults.update(kwargs)
        admin = AdminUser(**defaults)
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        return admin

    return _make


def _header(admin: AdminUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_admin_access_token(admin.id)}"}


# --- 권한 매트릭스 -------------------------------------------------------------


def test_viewer_can_only_read():
    """Viewer 에게 변경 권한이 하나라도 생기면 여기서 걸린다."""
    viewer = permissions_for(ROLE_VIEWER)
    assert viewer
    assert not [p for p in viewer if p.endswith(":write")]
    # 조회지만 Super Admin 전용인 예외(OI-MEM-004)
    assert MEMBER_UNMASK not in viewer
    assert MEMBER_READ in viewer


def test_super_admin_has_every_permission():
    assert permissions_for(ROLE_SUPER_ADMIN) == ALL_PERMISSIONS


def test_unknown_role_has_no_permission():
    """오타난 역할이 기본으로 뭔가 할 수 있으면, 매트릭스를 고치는 걸 잊은 순간이 곧 권한 상승이다."""
    assert permissions_for("superadmin") == frozenset()
    assert not has_permission("", MEMBER_WRITE)
    assert not has_permission(None, MEMBER_READ)  # type: ignore[arg-type]


def test_matrix_covers_declared_roles():
    assert set(PERMISSIONS_BY_ROLE) == {ROLE_SUPER_ADMIN, ROLE_VIEWER}


# --- 로그인 -------------------------------------------------------------------


async def test_login_returns_token_and_permissions(client, make_admin, audit_logs):
    admin = await make_admin(email="super@example.com")
    res = await client.post(
        "/admin/auth/login", json={"email": "super@example.com", "password": _PASSWORD}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == ROLE_SUPER_ADMIN
    assert body["must_change_password"] is False
    assert sorted(ALL_PERMISSIONS) == body["permissions"]

    claims = jwt.decode(body["token"], settings.secret_key, algorithms=[ALGORITHM])
    assert claims["scope"] == SCOPE_ADMIN
    assert claims["sub"] == str(admin.id)

    assert [log.action for log in await audit_logs()] == ["로그인"]


async def test_login_records_last_login_at(client, make_admin, audit_logs, db):
    admin = await make_admin(email="super@example.com")
    assert admin.last_login_at is None

    await client.post(
        "/admin/auth/login", json={"email": "super@example.com", "password": _PASSWORD}
    )
    await db.refresh(admin)
    assert admin.last_login_at is not None


async def test_login_is_case_insensitive_on_email(client, make_admin, audit_logs):
    await make_admin(email="super@example.com")
    res = await client.post(
        "/admin/auth/login",
        json={"email": "  Super@Example.COM ", "password": _PASSWORD},
    )
    assert res.status_code == 200


@pytest.mark.parametrize(
    "email,password",
    [
        ("nobody@example.com", _PASSWORD),  # 없는 계정
        ("super@example.com", "wrong-password"),  # 틀린 비밀번호
    ],
)
async def test_login_failures_are_indistinguishable(
    client, make_admin, audit_logs, email, password
):
    """없는 계정과 틀린 비밀번호가 같은 답을 준다 — 다르면 계정 열거가 된다."""
    await make_admin(email="super@example.com")
    res = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert res.status_code == 401
    assert res.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않아요."


async def test_inactive_admin_cannot_log_in(client, make_admin, audit_logs):
    """비활성 계정도 같은 문구로 거절한다. "존재하지만 잠겼다"를 알려 줄 이유가 없다."""
    await make_admin(email="gone@example.com", is_active=False)
    res = await client.post(
        "/admin/auth/login", json={"email": "gone@example.com", "password": _PASSWORD}
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않아요."


async def test_login_failure_is_audited_without_plaintext_email(
    client, make_admin, audit_logs
):
    await make_admin(email="super@example.com")
    await client.post(
        "/admin/auth/login", json={"email": "super@example.com", "password": "nope"}
    )
    logs = await audit_logs()
    assert [log.action for log in logs] == ["로그인실패"]
    assert logs[0].after == {"email": "su***@example.com"}


async def test_repeated_failures_are_rate_limited(client, make_admin, audit_logs):
    """상한은 비밀번호 검증보다 먼저 본다 — 맞힌 순간 통과해 버리면 막는 의미가 없다."""
    await make_admin(email="super@example.com")
    payload = {"email": "super@example.com", "password": "nope"}
    for _ in range(5):
        assert (await client.post("/admin/auth/login", json=payload)).status_code == 401

    assert (await client.post("/admin/auth/login", json=payload)).status_code == 429
    # 올바른 비밀번호로도 잠겨 있어야 한다
    res = await client.post(
        "/admin/auth/login", json={"email": "super@example.com", "password": _PASSWORD}
    )
    assert res.status_code == 429


# --- 토큰 scope 분리 -----------------------------------------------------------


async def test_app_user_token_is_rejected_by_admin_api(client, make_user, make_admin):
    """앱 사용자 토큰으로 관리자 API 를 열 수 없다."""
    await make_admin()  # admin_users.id = 1
    user = await make_user()  # users.id = 1 — id 가 겹쳐도 통하면 안 된다
    res = await client.get(
        "/admin/me", headers={"Authorization": f"Bearer {create_access_token(user.id)}"}
    )
    assert res.status_code == 401


async def test_admin_token_is_rejected_by_app_api(client, make_user, make_admin):
    """반대 방향도 막는다 — 관리자 토큰이 같은 번호의 앱 사용자로 통하면 안 된다."""
    admin = await make_admin()
    await make_user()
    res = await client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {create_admin_access_token(admin.id)}"},
    )
    assert res.status_code == 401


async def test_admin_api_requires_a_token(client):
    assert (await client.get("/admin/me")).status_code == 401


async def test_deactivated_admin_token_stops_working(client, make_admin, db):
    """토큰을 이미 쥔 관리자라도 계정을 내리면 다음 요청부터 막힌다."""
    admin = await make_admin()
    headers = _header(admin)
    assert (await client.get("/admin/me", headers=headers)).status_code == 200

    admin.is_active = False
    await db.commit()
    assert (await client.get("/admin/me", headers=headers)).status_code == 401


async def test_idle_session_expires(client, make_admin):
    """유휴 2시간(OI-AUTH-002). 그 시점에 발급됐을 모습 그대로 토큰을 조립한다."""
    admin = await make_admin()
    issued_at = datetime.now(timezone.utc) - timedelta(
        minutes=settings.idle_timeout_minutes + 1
    )
    token = jwt.encode(
        {
            "sub": str(admin.id),
            "iat": issued_at,
            "auth_time": int(issued_at.timestamp()),
            "exp": issued_at + timedelta(minutes=settings.idle_timeout_minutes),
            "scope": SCOPE_ADMIN,
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    res = await client.get("/admin/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# --- 권한별 접근 (직접 URL 접근 포함) -------------------------------------------


async def test_super_admin_can_list_admins(client, make_admin):
    admin = await make_admin(email="super@example.com")
    res = await client.get("/admin/admins", headers=_header(admin))
    assert res.status_code == 200
    assert [row["email"] for row in res.json()] == ["super@example.com"]
    # 비밀번호 해시는 응답 스키마에 아예 없다
    assert "password_hash" not in res.json()[0]


async def test_viewer_gets_403_on_super_admin_url(client, make_admin):
    """직접 URL 접근 — 관리자 앱이 메뉴를 숨기든 말든 서버가 거절한다."""
    viewer = await make_admin(email="viewer@example.com", role=ROLE_VIEWER)
    res = await client.get("/admin/admins", headers=_header(viewer))
    assert res.status_code == 403


async def test_viewer_sees_only_read_permissions_in_me(client, make_admin):
    viewer = await make_admin(email="viewer@example.com", role=ROLE_VIEWER)
    res = await client.get("/admin/me", headers=_header(viewer))
    assert res.status_code == 200
    assert res.json()["permissions"] == sorted(permissions_for(ROLE_VIEWER))
    assert ADMIN_READ not in res.json()["permissions"]


# --- 최초 로그인 비밀번호 변경 강제 (D-8) --------------------------------------


async def test_bootstrap_password_blocks_every_screen(client, make_admin):
    admin = await make_admin(must_change_password=True)
    res = await client.get("/admin/admins", headers=_header(admin))
    assert res.status_code == 403
    assert res.headers.get("X-Password-Change-Required") == "1"


async def test_password_change_is_reachable_before_changing(
    client, make_admin, audit_logs, db
):
    """비밀번호 변경만은 열려 있어야 한다 — 권한 검사 뒤에 두면 영원히 못 바꾼다."""
    admin = await make_admin(must_change_password=True)
    res = await client.post(
        "/admin/me/password",
        headers=_header(admin),
        json={"current_password": _PASSWORD, "new_password": "new-password-1234"},
    )
    assert res.status_code == 204

    await db.refresh(admin)
    assert admin.must_change_password is False

    # 이제 화면이 열린다
    assert (await client.get("/admin/admins", headers=_header(admin))).status_code == 200
    assert [log.action for log in await audit_logs()] == ["비밀번호변경"]


async def test_password_change_requires_current_password(client, make_admin, audit_logs):
    admin = await make_admin(must_change_password=True)
    res = await client.post(
        "/admin/me/password",
        headers=_header(admin),
        json={"current_password": "wrong", "new_password": "new-password-1234"},
    )
    assert res.status_code == 400
    assert await audit_logs() == []


async def test_password_change_rejects_reusing_the_same_password(client, make_admin):
    admin = await make_admin(must_change_password=True)
    res = await client.post(
        "/admin/me/password",
        headers=_header(admin),
        json={"current_password": _PASSWORD, "new_password": _PASSWORD},
    )
    assert res.status_code == 400


async def test_short_new_password_is_rejected(client, make_admin):
    admin = await make_admin()
    res = await client.post(
        "/admin/me/password",
        headers=_header(admin),
        json={"current_password": _PASSWORD, "new_password": "short"},
    )
    assert res.status_code == 422


# --- 로그아웃 -----------------------------------------------------------------


async def test_logout_is_audited(client, make_admin, audit_logs):
    admin = await make_admin()
    res = await client.post("/admin/auth/logout", headers=_header(admin))
    assert res.status_code == 204
    assert [log.action for log in await audit_logs()] == ["로그아웃"]
