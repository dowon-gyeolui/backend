"""진단 엔드포인트 차단과 시크릿 마스킹.

`backend/CLAUDE.md` 가 약속한 두 가지를 깨지면 알 수 있게 고정한다:
  - 진단 엔드포인트(`/health/db`)는 production(debug=False)에서 404
  - DB URL 의 비밀번호는 로그·응답에 평문으로 나가지 않는다
"""

import pytest

from app.main import _redact_db_url


@pytest.mark.asyncio
async def test_health_is_public(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_db_is_hidden_in_production(client):
    """DEBUG=false 이므로 진단 엔드포인트는 존재를 숨긴다."""
    res = await client.get("/health/db")
    assert res.status_code == 404


def test_redact_hides_password():
    url = "postgresql+asyncpg://postgres:sup3rs3cret@db.example.supabase.co:5432/postgres"
    out = _redact_db_url(url)
    assert "sup3rs3cret" not in out
    assert out.startswith("postgresql+asyncpg://postgres:***@")


def test_redact_keeps_urls_without_credentials_intact():
    url = "sqlite+aiosqlite:///./dev.db"
    assert _redact_db_url(url) == url


def test_redact_never_leaks_on_malformed_input():
    assert "secret" not in _redact_db_url("not-a-url-secret")
