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


# Dev-only defensive migrations for SQLite. Production should use Alembic.
# Each tuple is (table_name, column_name, ddl_fragment).
_SQLITE_DEV_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "nickname", "VARCHAR(50)"),
    ("users", "photo_url", "VARCHAR(512)"),
]


async def _dev_migrate_sqlite(conn) -> None:
    """Add columns listed in _SQLITE_DEV_COLUMNS if they don't already exist.

    SQLAlchemy's `create_all` only creates missing TABLES, not missing columns.
    For MVP convenience this helper does best-effort ADD COLUMN on SQLite so
    existing dev DBs don't need manual migration between model updates.
    """
    for table, column, ddl in _SQLITE_DEV_COLUMNS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def init_db() -> None:
    """Create all tables + apply dev SQLite column patches. Called once at startup."""
    import app.models  # noqa: F401 — registers all ORM models with Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await _dev_migrate_sqlite(conn)
