"""DB 엔진/세션 초기화와 개발용 스키마 자동 마이그레이션(_DEV_COLUMNS)."""

from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy import text
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
    for table, column, ddl in _DEV_COLUMNS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

async def _dev_migrate_postgres() -> None:
    for table, column, ddl in _DEV_COLUMNS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
                )
        except Exception as exc:  # noqa: BLE001 — startup 을 막지 않는 게 우선
            print(f"[init_db] skip ALTER {table}.{column}: {exc}", flush=True)

async def init_db() -> None:
    import app.models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await _dev_migrate_sqlite(conn)
    elif engine.dialect.name == "postgresql":
        await _dev_migrate_postgres()