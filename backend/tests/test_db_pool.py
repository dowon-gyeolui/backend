"""커넥션 풀 설정과 풀 고갈 처리.

고정하려는 것 두 가지:
  - 풀·타임아웃 값이 전부 환경변수(settings)에서 온다 — 코드 수정 없이 조정 가능해야 한다
  - 풀이 고갈되면 500 이 아니라 503 + Retry-After 가 나간다
"""

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import settings
from app.core.security import create_access_token
from app.database import Base, _apply_statement_timeout, _engine_options, get_db
from app.main import app

_PG_URL = "postgresql+asyncpg://u:p@db.example.test:5432/postgres"


def test_pool_options_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "db_pool_size", 11)
    monkeypatch.setattr(settings, "db_max_overflow", 3)
    monkeypatch.setattr(settings, "db_pool_timeout_seconds", 4.5)
    monkeypatch.setattr(settings, "db_pool_recycle_seconds", 120)

    opts = _engine_options(_PG_URL)

    assert opts["pool_size"] == 11
    assert opts["max_overflow"] == 3
    assert opts["pool_timeout"] == 4.5
    assert opts["pool_recycle"] == 120
    assert opts["pool_pre_ping"] is True


def test_sqlite_gets_no_pool_options():
    """로컬·테스트 SQLite 에 Postgres 전용 옵션이 새지 않아야 한다."""
    assert "pool_size" not in _engine_options("sqlite+aiosqlite:///:memory:")


def test_statement_timeout_is_set_on_connect(monkeypatch):
    """커넥션이 열릴 때마다 서버측 statement_timeout 이 걸린다."""
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 7000)

    executed: list[str] = []

    class _RawConn:
        async def execute(self, sql):
            executed.append(sql)

    class _DBAPIConn:
        def run_async(self, fn):
            asyncio.run(fn(_RawConn()))

    _apply_statement_timeout(_DBAPIConn(), None)

    assert executed == ["SET statement_timeout = 7000"]


# --- 풀 고갈 -------------------------------------------------------------------

_POOL_TIMEOUT_S = 0.2
_HOLD_S = 1.0


@pytest_asyncio.fixture
async def busy_client(tmp_path):
    """커넥션 1개짜리 풀에 묶인 클라이언트.

    get_db 가 커넥션을 잡은 채 잠시 머무르므로, 동시에 들어온 다른 요청은
    실제로 풀 고갈(TimeoutError)을 맞는다.
    """
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=_POOL_TIMEOUT_S,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def _slow_db():
        async with maker() as session:
            await session.execute(text("SELECT 1"))  # 여기서 커넥션을 체크아웃한다
            await asyncio.sleep(_HOLD_S)
            yield session

    app.dependency_overrides[get_db] = _slow_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    await eng.dispose()


@pytest.mark.asyncio
async def test_pool_exhaustion_returns_503_not_500(busy_client):
    headers = {"Authorization": f"Bearer {create_access_token(999999)}"}

    responses = await asyncio.gather(
        *(busy_client.get("/users/me", headers=headers) for _ in range(3))
    )
    codes = [r.status_code for r in responses]

    assert 500 not in codes, f"풀 고갈이 서버 오류로 새어 나갔다: {codes}"
    assert 503 in codes, f"풀 고갈이 503 으로 표현되지 않았다: {codes}"

    busy = next(r for r in responses if r.status_code == 503)
    assert busy.headers["Retry-After"] == "5"
    assert busy.json()["detail"] == "서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."
    # 접속 정보가 섞여 나가지 않는지
    assert "sqlite" not in busy.text and "pool" not in busy.text.lower()
