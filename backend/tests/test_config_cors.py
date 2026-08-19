"""cors_origins 가 Capacitor 네이티브 앱의 WebView origin 을 항상 허용하는지 확인한다.

배포 환경변수 frontend_urls 에는 배포된 웹 도메인만 담긴다. 네이티브 앱은 웹 자산을
번들해 로컬에서 로드하므로 요청 Origin 이 WebView 기본값(Android https://localhost,
iOS capacitor://localhost)으로 온다 — frontend_urls 에 없으면 /auth/app/exchange 호출이
CORS 로 막혀 카카오 로그인 코드 교환이 조용히 실패한다(재현: 배포 도메인만 넣고
cors_origins 확인).
"""

from app.config import Settings


def test_cors_origins_always_includes_native_app_origins():
    settings = Settings(frontend_urls="https://thezami.io,https://www.thezami.io")

    origins = settings.cors_origins

    assert "https://localhost" in origins
    assert "capacitor://localhost" in origins


def test_cors_origins_does_not_duplicate_if_already_present():
    settings = Settings(frontend_urls="https://thezami.io,https://localhost")

    assert settings.cors_origins.count("https://localhost") == 1


def test_frontend_url_still_uses_first_deployed_domain():
    settings = Settings(frontend_urls="https://thezami.io,https://www.thezami.io")

    assert settings.frontend_url == "https://thezami.io"
