"""테스트 하네스 자체를 검증한다 — 운영 DB 에 붙지 않는가."""

import pytest

from app.config import settings
from tests.conftest import TEST_DB_URL


def test_settings_point_to_sqlite():
    assert settings.database_url == TEST_DB_URL


def test_settings_never_point_to_managed_postgres():
    url = settings.database_url.lower()
    for marker in ("supabase", "asyncpg", "amazonaws", "render.com", "@"):
        assert marker not in url, f"운영 DB 로 의심되는 문자열이 있습니다: {marker!r}"


def test_debug_is_off_by_default():
    """DEBUG 가 켜져 있으면 인증 우회 경로(get_current_user 의 X-Dev-User-Id)가 열린다."""
    assert settings.debug is False


@pytest.mark.asyncio
async def test_tables_are_created(engine):
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        names = {r[0] for r in rows}
    assert "users" in names


@pytest.mark.asyncio
async def test_foreign_keys_are_enforced(engine):
    """SQLite 는 FK 검사가 기본 OFF 라, 켜두지 않으면 운영(Postgres)에서만 터지는
    FK 위반을 테스트가 통과시킨다. 실제로 탈퇴 버그(T-B07)를 그렇게 놓쳤다."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        enabled = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert enabled == 1, "테스트 커넥션에 PRAGMA foreign_keys 가 꺼져 있습니다"
