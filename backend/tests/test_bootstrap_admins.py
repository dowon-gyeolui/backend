"""관리자 계정 부트스트랩 스크립트 (T-E00).

막고 싶은 사고 네 가지.

1. **부트스트랩 비밀번호로 계정이 그대로 운영에 들어가는 것.** 만들어진 계정은
   반드시 `must_change_password=True` 여야 한다(D-8).
2. **재실행이 계정 탈취 수단이 되는 것.** 두 번째 실행이 이미 바꾼 비밀번호를
   부트스트랩 값으로 되돌리면 안 된다.
3. **대문자 섞인 이메일로 저장돼 영영 로그인이 안 되는 것.** 로그인은 입력을
   소문자로 정규화한다(`routers/admin.py`).
4. **계정 발급이 감사 로그에 안 남는 것.** "누가 이 시스템에 들어올 수 있는가" 를
   바꾸는 일이라 기록이 없으면 나중에 되짚을 방법이 없다.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.admin_rbac import ROLE_SUPER_ADMIN
from app.core.security import hash_password, verify_password
from app.database import Base
from app.models.admin import AdminUser
from app.models.audit_log import AuditLog
from app.services import audit
from scripts.bootstrap_admins import (
    ADMINS,
    PASSWORD_ENV,
    bootstrap_admins,
    resolve_password,
)

_PASSWORD = "bootstrap-1234"


@pytest_asyncio.fixture
async def audit_logs(monkeypatch):
    """감사 로그를 별도 DB 로 받아 읽는다(`test_admin_rbac.py` 와 같은 이유).

    `record_admin_action` 은 호출자 세션을 쓰지 않으므로, 요청 세션과 커넥션을
    공유하지 않는 전용 엔진을 준다.
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


async def _all_admins(db) -> list[AdminUser]:
    result = await db.execute(select(AdminUser).order_by(AdminUser.id))
    return list(result.scalars())


# --- 확정값 -------------------------------------------------------------------


def test_confirmed_accounts_are_three_lowercase_emails():
    """확정값은 3개이고 이메일은 전부 소문자·중복 없음(2026-08-19 사용자 확정)."""
    emails = [email for email, _ in ADMINS]
    assert len(ADMINS) == 3
    assert emails == [e.lower() for e in emails]
    assert len(set(emails)) == 3
    assert all(name for _, name in ADMINS)


# --- 생성 ---------------------------------------------------------------------


async def test_creates_three_super_admins_requiring_password_change(db, audit_logs):
    created, skipped = await bootstrap_admins(db, _PASSWORD)

    assert created == [email for email, _ in ADMINS]
    assert skipped == []

    admins = await _all_admins(db)
    assert len(admins) == 3
    for admin, (email, name) in zip(admins, ADMINS):
        assert admin.email == email
        assert admin.name == name
        assert admin.role == ROLE_SUPER_ADMIN
        assert admin.is_active is True
        # 이게 False 로 만들어지면 부트스트랩 비밀번호가 그대로 운영 계정이 된다.
        assert admin.must_change_password is True
        assert verify_password(_PASSWORD, admin.password_hash)


async def test_password_is_never_stored_in_plaintext(db, audit_logs):
    """해시는 계정마다 달라야 한다 — 같으면 DB 유출 시 '셋이 같은 비밀번호'까지 샌다."""
    await bootstrap_admins(db, _PASSWORD)

    admins = await _all_admins(db)
    hashes = [admin.password_hash for admin in admins]
    assert _PASSWORD not in hashes
    assert len(set(hashes)) == 3


# --- 재실행 -------------------------------------------------------------------


async def test_rerun_creates_nothing_and_keeps_changed_password(db, audit_logs):
    await bootstrap_admins(db, _PASSWORD)

    # 첫 관리자가 최초 로그인에서 비밀번호를 바꾼 상태를 흉내낸다.
    admins = await _all_admins(db)
    changed = admins[0]
    changed.password_hash = hash_password("나중에-바꾼-비밀번호")
    changed.must_change_password = False
    await db.commit()

    created, skipped = await bootstrap_admins(db, _PASSWORD)

    assert created == []
    assert skipped == [email for email, _ in ADMINS]
    assert len(await _all_admins(db)) == 3

    await db.refresh(changed)
    assert verify_password("나중에-바꾼-비밀번호", changed.password_hash)
    assert not verify_password(_PASSWORD, changed.password_hash)
    assert changed.must_change_password is False


# --- 감사 로그 ----------------------------------------------------------------


async def test_creation_is_audited_without_plaintext_pii(db, audit_logs):
    await bootstrap_admins(db, _PASSWORD)

    logs = await audit_logs()
    assert len(logs) == 3

    admins = await _all_admins(db)
    for log, admin, (email, name) in zip(logs, admins, ADMINS):
        assert log.action == "계정생성"
        assert log.target_type == "admin"
        assert log.target_id == str(admin.id)
        assert log.after["role"] == ROLE_SUPER_ADMIN
        # 마스킹을 거쳐 들어간다 — 감사 로그가 개인정보 사본이 되면 안 된다.
        assert log.after["email"] != email
        assert log.after["name"] != name
        assert _PASSWORD not in str(log.after)


async def test_rerun_does_not_add_audit_rows(db, audit_logs):
    await bootstrap_admins(db, _PASSWORD)
    await bootstrap_admins(db, _PASSWORD)

    assert len(await audit_logs()) == 3


# --- 비밀번호 결정 ------------------------------------------------------------


def test_password_comes_from_env_when_given(monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "사람이-정한-값")
    assert resolve_password() == "사람이-정한-값"


def test_password_is_random_when_env_is_absent(monkeypatch):
    """환경변수가 없으면 임의값. 코드에 박힌 기본값이 있으면 여기서 걸린다."""
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    first, second = resolve_password(), resolve_password()
    assert first != second
    assert len(first) >= 12
