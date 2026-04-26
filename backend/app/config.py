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
