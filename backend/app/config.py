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

    # 커넥션 풀·타임아웃. PostgreSQL 일 때만 쓰인다(SQLite 는 풀이 의미 없다).
    # 값을 바꾸기 전에 app/database.py 의 동시 커넥션 상한 계산식을 읽을 것.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: float = 10.0
    db_pool_recycle_seconds: int = 300
    # 서버측 statement_timeout(ms). 0 이면 걸지 않는다.
    db_statement_timeout_ms: int = 15000

    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://localhost:8000/auth/kakao/callback"
    kakao_admin_key: str = ""

    secret_key: str = "dev-secret-key-change-in-production"
    # 세션 절대 상한 — 재로그인 없이 세션이 이어질 수 있는 최대 기간.
    access_token_expire_minutes: int = 60 * 24 * 7
    # 유휴 만료 — 마지막 활동 이후 이만큼 지나면 토큰이 죽는다 (OI-AUTH-002: 2시간).
    idle_timeout_minutes: int = 120
    # 민감 액션(탈퇴·결제 승인·자격증명 변경)에 요구하는 "최근 인증" 유효 시간.
    reauth_window_minutes: int = 10

    toss_secret_key: str = ""

    redis_url: str = ""

    sentry_dsn: str = ""

    firebase_service_account_json: str = ""

    # 가입(온보딩 완료) 알림 메일. SMTP 가 아니라 HTTP API(Resend)를 쓴다 —
    # Render 는 아웃바운드 SMTP(25/465/587)를 막는 경우가 있어 조용히 실패한다.
    # 키나 수신 주소가 비어 있으면 알림은 비활성화되고 온보딩에는 영향이 없다.
    alert_email_api_key: str = ""
    alert_email_to: str = ""
    alert_email_from: str = "MeloBe 알림 <onboarding@resend.dev>"

    frontend_urls: str = "http://localhost:3000"

    # 기본값은 운영 기준이다. 켜려면 환경변수로 명시해야 한다 — 설정을 빠뜨렸을 때
    # 열리는 쪽이 아니라 닫히는 쪽으로 실패해야 한다.
    debug: bool = False

    # 개발용 무인증 우회(X-Dev-User-Id, core/deps.py)를 여는 **별도** 스위치.
    # debug 하나로는 열리지 않는다 — debug 는 진단 라우트 등 다른 용도로도 켜는 값이라
    # 거기에 인증 우회까지 딸려 열리면 사고가 된다(strix HIGH, T-H06).
    allow_dev_auth: bool = False

    # 푸시 등 기능 테스트용. true 면 매칭 후보에서 이성 필터를 걷어낸다.
    # 운영에서는 반드시 false 여야 한다.
    allow_same_gender_match: bool = False

    @property
    def frontend_url(self) -> str:
        return self.frontend_urls.split(",")[0].strip()

    @property
    def cors_origins(self) -> list[str]:
        return [u.strip() for u in self.frontend_urls.split(",") if u.strip()]

settings = Settings()