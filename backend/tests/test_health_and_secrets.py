"""진단 엔드포인트 차단과 시크릿 마스킹 (T-B05).

`backend/CLAUDE.md` 가 약속한 두 가지를 깨지면 알 수 있게 고정한다:
  - 진단·테스트 엔드포인트는 production(debug=False)에서 404 — 존재 자체를 숨긴다
  - `.env` 의 시크릿(DATABASE_URL·API 키)은 응답·로그에 평문으로 나가지 않는다

conftest 가 `DEBUG=false` 를 강제하므로 이 파일의 테스트는 운영과 같은 조건에서 돈다.
"""

import pytest

from app.core.redact import redact_secrets, redact_url_credentials
from app.main import app

# 개발·테스트 전용 엔드포인트. 운영에서는 전부 404 여야 한다.
_DIAGNOSTIC_ENDPOINTS = [
    ("GET", "/health/db"),            # knowledge_chunks 적재 현황
    ("POST", "/users/me/upgrade-demo"),  # 유료 플래그 무료 부여
    ("POST", "/payments/test-topup"),    # 스타 무료 지급
]

# 목록에 없는 진단 엔드포인트가 늘어나는 것을 잡는 표식. 경로에 이게 들어가면 목록에 있어야 한다.
_DIAGNOSTIC_PATH_MARKERS = ("/health/db", "demo", "test-", "debug", "diag")


@pytest.mark.asyncio
async def test_health_is_public(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _DIAGNOSTIC_ENDPOINTS)
async def test_diagnostic_endpoints_are_404_in_production(
    client, make_user, auth_header, method, path
):
    """DEBUG=false 이므로 진단·테스트 엔드포인트는 전부 존재를 숨긴다.

    인증까지 붙여서 호출한다 — 401 로 막히는 것과 404 로 숨는 것은 다르다.
    """
    user = await make_user()
    res = await client.request(method, path, headers=auth_header(user))
    assert res.status_code == 404, f"{method} {path} 가 운영에서 열려 있다"


def test_no_undeclared_diagnostic_routes():
    """새 진단 엔드포인트가 목록·차단 없이 늘어나는 것을 막는 래칫."""
    declared = {path for _, path in _DIAGNOSTIC_ENDPOINTS}
    found = {
        route.path
        for route in app.routes
        if any(marker in route.path for marker in _DIAGNOSTIC_PATH_MARKERS)
    }
    assert found <= declared, (
        f"위 목록에 없는 진단 엔드포인트: {sorted(found - declared)} — "
        "운영에서 404 인지 확인하고 _DIAGNOSTIC_ENDPOINTS 에 추가할 것"
    )


@pytest.mark.asyncio
async def test_dev_auth_bypass_is_closed_in_production(client):
    """`X-Dev-User-Id` 무인증 우회(core/deps.py)는 debug 에서만 열린다."""
    res = await client.get("/users/me", headers={"X-Dev-User-Id": "1"})
    assert res.status_code == 401


# --- 시크릿 마스킹 -------------------------------------------------------------


def test_redact_hides_password():
    url = "postgresql+asyncpg://postgres:sup3rs3cret@db.example.supabase.co:5432/postgres"
    out = redact_url_credentials(url)
    assert "sup3rs3cret" not in out
    assert out.startswith("postgresql+asyncpg://postgres:***@")


def test_redact_keeps_urls_without_credentials_intact():
    url = "sqlite+aiosqlite:///./dev.db"
    assert redact_url_credentials(url) == url


def test_redact_never_leaks_on_malformed_input():
    assert "secret" not in redact_url_credentials("not-a-url-secret")


def test_redact_secrets_masks_configured_database_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+asyncpg://postgres:pw-from-env@db.example.supabase.co:5432/postgres",
    )
    out = redact_secrets(f"connection failed: {settings.database_url}")
    assert "pw-from-env" not in out
    assert "db.example.supabase.co" not in out


def test_redact_secrets_masks_api_keys(monkeypatch):
    # 실제 키 형태(`sk-...`)를 쓰지 않는다 — verify.sh 의 시크릿 스캔이 잡는다.
    openai_key = "openai-key-fixture-not-real"
    cloudinary_secret = "cloudinary-api-secret-fixture"
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    monkeypatch.setenv("CLOUDINARY_API_SECRET", cloudinary_secret)

    out = redact_secrets(f"Error calling {openai_key} with {cloudinary_secret}")
    assert openai_key not in out
    assert cloudinary_secret not in out


def test_redact_secrets_masks_unknown_url_credentials():
    """설정값과 글자가 달라도 URL 에 박힌 비밀번호는 가린다."""
    out = redact_secrets("cloudinary://123456789:AbCdEf-unseen-secret@mycloud 로 접속 실패")
    assert "AbCdEf-unseen-secret" not in out
    assert "cloudinary://123456789:***@mycloud" in out


def test_redact_secrets_leaves_ordinary_text_alone(monkeypatch):
    """빈 설정값이 무관한 문자열까지 지워버리지 않는다."""
    from app.config import settings

    monkeypatch.setattr(settings, "toss_secret_key", "")
    monkeypatch.setattr(settings, "kakao_admin_key", "")
    text = "이미지 업로드에 실패했어요."
    assert redact_secrets(text) == text


@pytest.mark.asyncio
async def test_upload_failure_never_leaks_secrets(
    client, make_user, auth_header, monkeypatch, capfd
):
    """저장소 예외에 시크릿이 섞여 있어도 응답·로그로 나가지 않는다.

    cloudinary 는 설정이 잘못되면 접속 URL 이 통째로 들어간 예외를 던진다.
    그 문자열이 502 응답 본문으로 흘러나가던 것이 T-B05 에서 고친 실제 결함이다.
    """
    from app.config import settings

    leaked_db_url = "postgresql+asyncpg://postgres:db-pw-leak@db.example.supabase.co:5432/postgres"
    monkeypatch.setattr(settings, "database_url", leaked_db_url)
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://999:cloud-secret-leak@mycloud")

    def _boom(*args, **kwargs):
        raise RuntimeError(
            f"upload failed: CLOUDINARY_URL=cloudinary://999:cloud-secret-leak@mycloud "
            f"DATABASE_URL={leaked_db_url}"
        )

    monkeypatch.setattr("app.routers.users.upload_image_full", _boom)

    user = await make_user()
    res = await client.post(
        "/users/me/photo",
        headers=auth_header(user),
        files={"file": ("p.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )

    assert res.status_code == 502
    body = res.text
    logged = capfd.readouterr().out
    for secret in ("db-pw-leak", "cloud-secret-leak", leaked_db_url):
        assert secret not in body, f"응답에 시크릿 노출: {secret}"
        assert secret not in logged, f"로그에 시크릿 노출: {secret}"
