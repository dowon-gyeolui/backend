"""DB 엔진/세션 초기화와 기동 시 스키마 리비전(Alembic) 확인."""

from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_engine_kwargs: dict = {"echo": settings.debug}
if settings.database_url.startswith("postgresql"):
    _engine_kwargs.update(
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",
        },
    )
engine = create_async_engine(settings.database_url, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

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