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
    # 출생지 — 사주 시각 보정용
    ("users", "birth_place", "VARCHAR(50)"),
    # 채팅 미디어 첨부
    ("messages", "media_url", "VARCHAR(512)"),
    ("messages", "media_type", "VARCHAR(16)"),
    # 채팅 읽음 상태 + 소프트 leave (per-user). 안읽은 메시지 개수 산정 +
    # 카카오톡 스타일 채팅방 나가기. SQLite 와 PostgreSQL 둘 다에서
    # 안전하게 동작하도록 BOOLEAN 의 default 는 FALSE 키워드 사용
    # (SQLite 3.23+ 와 Postgres 모두 인식).
    ("chat_threads", "user_a_last_read_id", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_threads", "user_b_last_read_id", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_threads", "user_a_left", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("chat_threads", "user_b_left", "BOOLEAN NOT NULL DEFAULT FALSE"),
    # 채팅 모더레이션 정지 만료 시각 (NULL = 정지 아님). TIMESTAMP 문법은
    # SQLite·PostgreSQL 모두 인식. timezone 정보는 application 측에서
    # datetime.now(timezone.utc) 로 처리하므로 별도 WITH TIME ZONE 불필요.
    ("users", "chat_suspended_until", "TIMESTAMP"),
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