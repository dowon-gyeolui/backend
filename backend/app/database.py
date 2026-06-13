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

_DEV_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "nickname", "VARCHAR(50)"),
    ("users", "photo_url", "VARCHAR(512)"),
    ("users", "bio", "VARCHAR(120)"),
    ("users", "height_cm", "INTEGER"),
    ("users", "mbti", "VARCHAR(4)"),
    ("users", "job", "VARCHAR(50)"),
    ("users", "region", "VARCHAR(50)"),
    ("users", "smoking", "VARCHAR(20)"),
    ("users", "drinking", "VARCHAR(20)"),
    ("users", "religion", "VARCHAR(20)"),
    ("users", "birth_place", "VARCHAR(50)"),
    ("messages", "media_url", "VARCHAR(512)"),
    ("messages", "media_type", "VARCHAR(16)"),
    ("chat_threads", "user_a_last_read_id", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_threads", "user_b_last_read_id", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_threads", "user_a_left", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("chat_threads", "user_b_left", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("users", "chat_suspended_until", "TIMESTAMP"),
    ("user_photos", "is_face_verified", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("users",  "star_balance", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "pref_age_min", "INTEGER"),
    ("users", "pref_age_max", "INTEGER"),
    ("users", "pref_region", "VARCHAR(50)"),
    ("users", "pref_height_min", "INTEGER"),
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
    import app.models
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await _dev_migrate_sqlite(conn)
        elif engine.dialect.name == "postgresql":
            await _dev_migrate_postgres(conn)