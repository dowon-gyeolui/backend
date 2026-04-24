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

    debug: bool = True


settings = Settings()
