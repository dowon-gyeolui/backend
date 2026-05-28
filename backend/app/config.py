"""환경설정(pydantic-settings).

.env 파일에서 다음 값들을 읽어 들인다.
  - database_url: SQLite(기본) 또는 PostgreSQL 접속 문자열
  - kakao_client_id / kakao_client_secret / kakao_redirect_uri:
    카카오 OAuth 설정
  - kakao_admin_key: 탈퇴 시 unlink API 호출용 어드민 키
  - secret_key / access_token_expire_minutes: JWT 발급용
  - frontend_urls: CORS 허용 및 OAuth 후 리다이렉트 대상 (콤마 구분)

SQLite 기본 경로는 backend/ 디렉터리를 기준으로 절대 경로로 고정해
실행 위치(uvicorn / 스크립트)에 따라 다른 DB 파일을 바라보지 않게 한다.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor the default SQLite path to backend/ so the DB file is the same
# regardless of where `uvicorn` or any script is launched from.
#   backend/app/config.py  →  .parent = backend/app  →  .parent = backend
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SQLITE_URL = (
    f"sqlite+aiosqlite:///{(_BACKEND_ROOT / 'jamidusu_dev.db').as_posix()}"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Absolute-anchored SQLite default — immune to cwd differences between
    # `python scripts/ingest_jsonl_to_db.py` and `uvicorn app.main:app`.
    # Override via DATABASE_URL in .env for PostgreSQL / other engines.
    database_url: str = _DEFAULT_SQLITE_URL

    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://localhost:8000/auth/kakao/callback"
    # 어드민 키 — 사용자 탈퇴 시 unlink API 호출용. 비어 있으면 unlink
    # 호출이 스킵되고, 같은 kakao_id 로 재가입할 때 동의 화면이 안 뜸.
    # Kakao Developers → 내 애플리케이션 → 앱 설정 → 앱 키 → 어드민 키.
    kakao_admin_key: str = ""

    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Frontend origin(s) — comma-separated. The FIRST entry is used for the
    # post-login OAuth redirect; ALL entries are added to the CORS allow-list.
    # Example for production:
    #   FRONTEND_URLS=https://zami.vercel.app,http://localhost:3000
    frontend_urls: str = "http://localhost:3000"

    debug: bool = True

    @property
    def frontend_url(self) -> str:
        """Primary frontend URL — used for OAuth post-login redirect."""
        return self.frontend_urls.split(",")[0].strip()

    @property
    def cors_origins(self) -> list[str]:
        """All allowed CORS origins (handles dev + prod simultaneously)."""
        return [u.strip() for u in self.frontend_urls.split(",") if u.strip()]


settings = Settings()
