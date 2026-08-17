"""테스트 공통 픽스처.

가장 중요한 책임은 **운영 DB 에 절대 붙지 않게 하는 것**이다.
`app.config.settings` 와 `app.database.engine` 은 모듈 임포트 시점에 만들어지므로,
`app.*` 를 임포트하기 전에 환경변수를 먼저 덮어써야 한다. 그래서 이 파일 맨 위에서
os.environ 을 세팅한 뒤에야 app 모듈을 임포트한다. 순서를 바꾸지 말 것.
"""

import os

# --- app 임포트보다 반드시 먼저 -------------------------------------------------
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DEBUG"] = "false"          # 테스트 기본값은 운영과 같게 둔다
os.environ["REDIS_URL"] = ""
os.environ["SENTRY_DSN"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["TOSS_SECRET_KEY"] = ""
os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"] = ""
os.environ["ALERT_EMAIL_API_KEY"] = ""   # 테스트가 실제 메일을 보내지 않도록
os.environ["ALERT_EMAIL_TO"] = ""
os.environ["ALLOW_SAME_GENDER_MATCH"] = "false"
# .env 를 읽어 위 값을 덮어쓰지 않도록(개발자 PC 에 운영 .env 가 있을 수 있다)
os.environ["ENV_FILE"] = ""

from datetime import date  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.database import Base, get_db  # noqa: E402

# Base.metadata 에 전체 테이블을 등록한다. `import app.models` 로 쓰면 아래에서 가져오는
# FastAPI 인스턴스 `app` 을 패키지 모듈로 덮어써 버리므로 별칭으로 임포트한다.
import app.models as _all_models  # noqa: E402,F401

from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402


TEST_DB_URL = _TEST_DB_URL


@pytest.fixture(scope="session", autouse=True)
def _assert_not_production():
    """운영 DB 로 테스트가 도는 사고를 원천 차단한다."""
    url = settings.database_url
    assert url.startswith("sqlite"), f"테스트는 SQLite 로만 돈다. 현재: {url!r}"
    assert "supabase" not in url and "postgres" not in url, f"운영 DB 의심: {url!r}"


@pytest_asyncio.fixture
async def engine():
    # in-memory SQLite 는 커넥션마다 별도 DB 라서 StaticPool 이 필요하다.
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite 는 외래키 제약을 커넥션마다 명시적으로 켜야 검사한다(기본 OFF).
    # 꺼둔 채로 두면 운영(Postgres)에서 ForeignKeyViolation 으로 터지는 코드가
    # 테스트에서는 조용히 통과한다 — 실제로 탈퇴 버그(T-B07)를 이 때문에 놓쳤다.
    @event.listens_for(eng.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    """앱의 get_db 를 테스트 세션으로 갈아끼운 HTTP 클라이언트."""

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_user(db):
    """매칭 후보 자격(생년월일·사진·성별)을 기본으로 갖춘 사용자를 만든다."""

    async def _make(**kwargs):
        defaults = dict(
            kakao_id=f"test_{id(kwargs)}_{len(kwargs)}",
            birth_date=date(1995, 3, 20),
            gender="female",
            photo_url="https://example.test/p.jpg",
            nickname="테스트",
            region="서울",
            height_cm=165,
        )
        defaults.update(kwargs)
        user = User(**defaults)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    return _make


@pytest.fixture
def auth_header():
    """해당 사용자로 인증된 Authorization 헤더."""

    def _header(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _header
