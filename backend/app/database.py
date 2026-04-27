from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# Dev-only defensive migrations. Each tuple is (table, column, ddl_fragment).
# Both SQLite and PostgreSQL get the same column set — Alembic should replace
# this once the schema stops moving.
_DEV_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "nickname", "VARCHAR(50)"),
    ("users", "photo_url", "VARCHAR(512)"),
    # 한 줄 자기소개
    ("users", "bio", "VARCHAR(120)"),
    # 기본 정보
    ("users", "height_cm", "INTEGER"),
    ("users", "mbti", "VARCHAR(4)"),
    ("users", "job", "VARCHAR(50)"),
    ("users", "region", "VARCHAR(50)"),
    ("users", "smoking", "VARCHAR(20)"),
    ("users", "drinking", "VARCHAR(20)"),
    ("users", "religion", "VARCHAR(20)"),
]


async def _dev_migrate_sqlite(conn) -> None:
    """Add missing columns on SQLite. PRAGMA-based existence check."""
    for table, column, ddl in _DEV_COLUMNS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def _dev_migrate_postgres(conn) -> None:
    """Add missing columns on PostgreSQL using IF NOT EXISTS (PG 9.6+)."""
    for table, column, ddl in _DEV_COLUMNS:
        await conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        )


async def init_db() -> None:
    """Create all tables + apply dev column patches. Called once at startup."""
    import app.models  # noqa: F401 — registers all ORM models with Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await _dev_migrate_sqlite(conn)
        elif engine.dialect.name == "postgresql":
            await _dev_migrate_postgres(conn)