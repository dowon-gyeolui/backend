"""Alembic 실행 환경 — async 엔진(asyncpg/aiosqlite) 위에서 마이그레이션을 돌린다."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from app.config import settings
from app.database import Base

# Base.metadata 에 전체 테이블을 등록하기 위한 import.
# `import app.models` 로 쓰면 FastAPI 인스턴스 `app` 을 패키지 모듈로 덮어쓰므로 별칭을 쓴다.
import app.models as _all_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """테스트가 -x/set_main_option 으로 넘긴 URL 이 있으면 그것을, 없으면 설정값을 쓴다."""
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite 는 ALTER COLUMN/DROP COLUMN 이 없다. 테스트가 SQLite 로 도는 이상
        # 컬럼 변경 리비전이 나오면 batch 모드가 없으면 그 자리에서 깨진다.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
