"""트랜잭션마다 `app.current_user_id` 를 다시 거는 훅 (RLS 전제 — T-B10).

고정하려는 성질 셋:
  - 한 요청 안에서 여러 번 commit 해도 트랜잭션이 새로 열릴 때마다 다시 걸린다
  - **커넥션을 공유하는 서로 다른 사용자의 연속 요청에서 값이 섞이지 않는다**
  - 인증되지 않은 컨텍스트에서는 빈 값이 걸려 직전 사용자 값이 남지 않는다

Postgres 없이 이걸 보기 위해 SQLite 에 `set_config` 를 UDF 로 심어 호출 인자를 기록한다.
`is_local=true` 가 진짜 트랜잭션 스코프라는 것은 Postgres 가 보장하는 부분이고,
여기서는 우리가 그 인자를 true 로 넘기는지까지만 검증한다.
"""

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.deps import get_current_user
from app.core.request_context import CurrentUserContextMiddleware, current_user_id
from app.database import AppSession, _bind_current_user, get_db
from app.models.user import User


@pytest_asyncio.fixture
async def shared_conn(tmp_path):
    """커넥션 1개를 모든 세션이 공유하는 엔진 + 프로덕션과 같은 훅이 걸린 세션 팩토리.

    풀 크기가 1이라 연속된 요청이 실제로 같은 커넥션을 재사용한다 — 세션 변수가 새는
    사고가 일어나는 바로 그 조건이다.
    """
    calls: list[tuple[str, str, bool]] = []
    connects: list[object] = []

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'ctx.db'}",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _install_set_config(dbapi_conn, _record):
        connects.append(dbapi_conn)

        def set_config(name, value, is_local):
            calls.append((name, value, bool(is_local)))
            return value

        dbapi_conn.create_function("set_config", 3, set_config)

    event.listen(AppSession, "after_begin", _bind_current_user)
    maker = async_sessionmaker(eng, expire_on_commit=False, sync_session_class=AppSession)
    try:
        yield maker, calls, connects
    finally:
        event.remove(AppSession, "after_begin", _bind_current_user)
        await eng.dispose()


async def _one_transaction(maker, user_id: int | None) -> None:
    """사용자 한 명의 요청 하나를 흉내낸다."""
    token = current_user_id.set(user_id)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()
    finally:
        current_user_id.reset(token)


async def test_rebinds_on_every_transaction(shared_conn):
    """commit 이 트랜잭션을 끝내도 다음 트랜잭션에서 값이 다시 걸린다."""
    maker, calls, _ = shared_conn

    token = current_user_id.set(7)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()
            # 같은 요청 안의 두 번째 commit — 여기서 SET LOCAL 값이 사라진다
            await session.execute(text("SELECT 1"))
            await session.commit()
    finally:
        current_user_id.reset(token)

    assert [value for _, value, _ in calls] == ["7", "7"]
    assert all(name == "app.current_user_id" for name, _, _ in calls)
    assert all(is_local for *_, is_local in calls), "세션 스코프로 걸면 다음 요청으로 샌다"


async def test_value_does_not_leak_between_users(shared_conn):
    """같은 커넥션을 물려받은 다음 요청이 앞 사용자의 값을 보지 못한다."""
    maker, calls, connects = shared_conn

    await _one_transaction(maker, 1)
    await _one_transaction(maker, 2)
    await _one_transaction(maker, None)  # 로그인하지 않은 요청

    assert len(connects) == 1, "커넥션이 공유되지 않으면 이 테스트는 아무것도 증명하지 않는다"
    assert [value for _, value, _ in calls] == ["1", "2", ""]


async def test_hook_not_registered_on_sqlite():
    """set_config 가 없는 DB 에는 훅이 붙지 않는다(로컬 SQLite 개발이 죽지 않아야 한다)."""
    assert not event.contains(AppSession, "after_begin", _bind_current_user)


@pytest_asyncio.fixture
async def ctx_client(db):
    """`get_current_user` 가 컨텍스트를 채우는지 보기 위한 최소 앱.

    운영 앱(`app.main.app`)에 테스트용 라우트를 붙이지 않는다 —
    `test_health_and_secrets` 가 라우트 목록을 래칫으로 검사한다.
    """
    probe = FastAPI()
    probe.add_middleware(CurrentUserContextMiddleware)

    @probe.get("/ctx")
    async def _ctx(user: User = Depends(get_current_user)):
        return {"ctx": current_user_id.get(), "user_id": user.id}

    async def _override():
        yield db

    probe.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=probe)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_context_follows_authenticated_user(ctx_client, make_user, auth_header):
    a = await make_user(kakao_id="ctx_a")
    b = await make_user(kakao_id="ctx_b")

    first = await ctx_client.get("/ctx", headers=auth_header(a))
    second = await ctx_client.get("/ctx", headers=auth_header(b))

    assert first.json() == {"ctx": a.id, "user_id": a.id}
    assert second.json() == {"ctx": b.id, "user_id": b.id}
    assert current_user_id.get() is None, "요청이 끝난 뒤 값이 바깥으로 새어 나갔다"
