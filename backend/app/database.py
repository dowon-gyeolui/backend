"""DB 엔진/세션 초기화와 기동 시 스키마 리비전(Alembic) 확인."""

from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

from app.config import settings
from app.core.request_context import current_user_id


def _engine_options(url: str) -> dict:
    """create_async_engine 옵션. 풀 관련 값은 전부 환경변수에서 온다.

    SQLite(로컬·테스트)는 풀 설정이 의미가 없어 echo 만 준다.
    """
    options: dict = {"echo": settings.debug}
    if not url.startswith("postgresql"):
        return options

    # ── 동시 커넥션 상한 계산 ────────────────────────────────────────────────
    # 이 식을 넘기면 DB 가 새 연결을 거절해 앱 전체가 죽는다. 풀을 키우기 전에 반드시 계산할 것.
    #
    #   인스턴스당 최대 = (DB_POOL_SIZE + DB_MAX_OVERFLOW) × uvicorn 워커 수
    #   전체          = 인스턴스당 최대 × Render 인스턴스 수
    #   전체 + 여유분 5(마이그레이션·psql·Supabase Studio) ≤ DB 커넥션 상한
    #
    # DB 커넥션 상한은 접속 경로에 따라 다르다:
    #   · Session pooler(현재)  → Supabase Dashboard > Database > Connection pooling 의 "Pool Size"
    #   · Direct connection     → 인스턴스 크기별 max_connections (여유분을 뺀 값)
    #
    # 기본값 5+5 는 워커 1개 · 인스턴스 1개(= 최대 10) 기준이다.
    # 인스턴스나 워커를 늘리면 이 식으로 다시 계산해 환경변수를 낮춰야 한다.
    options.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # 풀이 고갈됐을 때 무한정 매달리지 않는다. 여기서 TimeoutError 가 나면
        # main.py 의 핸들러가 503 + Retry-After 로 바꿔 내보낸다.
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",
        },
    )
    return options


engine = create_async_engine(settings.database_url, **_engine_options(settings.database_url))


def _apply_statement_timeout(dbapi_connection, _record) -> None:
    """새 커넥션마다 서버측 statement_timeout 을 건다.

    startup parameter(`server_settings`)로 넣지 않는 이유: 풀러가 허용하지 않는
    startup parameter 는 접속 자체를 실패시킨다. 평범한 SET 쿼리로 걸면
    Session pooler 와 Direct connection 양쪽에서 똑같이 동작한다.
    pool_pre_ping 으로 커넥션이 다시 열려도 이 훅이 다시 돈다.
    """
    dbapi_connection.run_async(
        lambda conn: conn.execute(
            f"SET statement_timeout = {settings.db_statement_timeout_ms}"
        )
    )


if settings.database_url.startswith("postgresql") and settings.db_statement_timeout_ms > 0:
    event.listen(engine.sync_engine, "connect", _apply_statement_timeout)


class AppSession(Session):
    """앱이 쓰는 동기 세션 클래스. `after_begin` 훅을 이 클래스에만 건다.

    전역 `Session` 에 걸면 Alembic·테스트 하네스처럼 무관한 세션까지 잡혀
    set_config 가 없는 DB 에서 터진다.
    """


# `SET LOCAL` 은 바인드 파라미터를 못 받는다. set_config(..., is_local=true) 가
# SET LOCAL 과 같은 트랜잭션 스코프이면서 값은 파라미터로 넘길 수 있다.
_SET_CURRENT_USER = text("SELECT set_config('app.current_user_id', :user_id, true)")


def _bind_current_user(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    """트랜잭션이 열릴 때마다 현재 사용자 id 를 트랜잭션 스코프 변수로 **다시** 건다.

    요청 시작에 한 번 거는 방식은 작동하지 않는다. 앱은 한 요청 안에서 여러 번 commit 하고
    (예: `routers/chat._enforce_chat_moderation`), commit 이 트랜잭션을 끝내면 값이 사라진다.
    반대로 세션 스코프 `SET` 을 쓰면 풀링된 커넥션을 타고 **다음 요청으로 값이 새어
    남의 데이터가 보인다**. 트랜잭션마다 다시 거는 이 훅만이 양쪽을 다 피한다.

    사용자가 없으면(로그인 전 요청·백그라운드 루프) 빈 문자열을 건다. 트랜잭션 스코프라
    안 걸어도 값이 남지는 않지만, "모든 트랜잭션은 자기 사용자로 명시적으로 덮어쓴다"는
    불변식이 있어야 나중에 이 코드를 고칠 때 실수가 드러난다. 대가는 트랜잭션당 왕복 1회다.
    """
    user_id = current_user_id.get()
    connection.execute(
        _SET_CURRENT_USER, {"user_id": "" if user_id is None else str(user_id)}
    )


AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
    sync_session_class=AppSession,
)

# SQLite(로컬·테스트)에는 set_config 가 없다. Postgres 에서만 건다.
if settings.database_url.startswith("postgresql"):
    event.listen(AppSession, "after_begin", _bind_current_user)


class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    # script_location 은 ini 에 상대경로로 적혀 있다. 어느 디렉터리에서 앱을 띄우든
    # 같은 곳을 가리키도록 절대경로로 고정한다.
    cfg.set_main_option("script_location", str(_ALEMBIC_INI.parent / "alembic"))
    return cfg

def _code_heads() -> set[str]:
    """리비전 파일들이 정의하는 head 집합."""
    from alembic.script import ScriptDirectory

    return set(ScriptDirectory.from_config(_alembic_config()).get_heads())

def _db_heads(conn: Connection) -> set[str]:
    """DB 의 alembic_version 이 가리키는 리비전 집합. 테이블이 없으면 빈 집합."""
    from alembic.runtime.migration import MigrationContext

    return set(MigrationContext.configure(conn).get_current_heads())

async def init_db() -> None:
    """스키마를 만들지 않는다. DB 리비전이 코드의 head 와 같은지 확인만 한다.

    예전에는 기동할 때마다 `create_all` + 하드코딩 ALTER 목록(_DEV_COLUMNS)을 돌렸다.
    컬럼 추가 외에는 아무것도 못 했고, 실패해도 print 만 하고 넘어가 스키마 드리프트가
    조용히 쌓였다. 이제 스키마 변경은 전부 Alembic 리비전으로만 하고, 여기서는
    어긋났는지 확인해 **어긋났으면 기동을 실패**시킨다.
    """
    code = _code_heads()
    async with engine.connect() as conn:
        db = await conn.run_sync(_db_heads)

    if db != code:
        raise RuntimeError(
            "DB 스키마 리비전이 코드와 다릅니다. 스키마 드리프트를 안고 기동하지 않습니다.\n"
            f"  DB   : {', '.join(sorted(db)) or '(alembic_version 없음)'}\n"
            f"  코드 : {', '.join(sorted(code)) or '(리비전 없음)'}\n"
            "다음 중 하나를 실행하세요 (backend/ 디렉터리에서):\n"
            "  · 스키마가 이미 있는 DB(운영·기존 개발 DB) → alembic stamp head\n"
            "  · 완전히 빈 DB / 리비전이 밀린 DB          → alembic upgrade head"
        )