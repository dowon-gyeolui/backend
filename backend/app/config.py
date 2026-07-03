"""환경변수 기반 앱 설정(Settings) 정의 및 전역 settings 인스턴스."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    database_url: str = _DEFAULT_SQLITE_URL

    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://localhost:8000/auth/kakao/callback"
    kakao_admin_key: str = ""

    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7

    toss_secret_key: str = ""

    frontend_urls: str = "http://localhost:3000"

    debug: bool = True

    @property
    def frontend_url(self) -> str:
        return self.frontend_urls.split(",")[0].strip()

    @property
    def cors_origins(self) -> list[str]:
        return [u.strip() for u in self.frontend_urls.split(",") if u.strip()]

settings = Settings()