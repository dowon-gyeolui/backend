"""FastAPI 앱 엔트리포인트: lifespan(DB 초기화, 음성 정리 루프), CORS, 라우터 등록."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.config import settings
from app.core.deps import (
    REAUTH_REQUIRED_HEADER,
    REFRESHED_TOKEN_HEADER,
)
from app.core.redact import redact_url_credentials
from app.core.request_context import CurrentUserContextMiddleware
from app.database import AsyncSessionLocal
from app.routers import (
    auth,
    chat,
    compatibility,
    knowledge,
    matching,
    payments,
    recommendations,
    reports,
    saju,
    stats,
    users,
)


# 저장소·문서·예제에 실려 있는 SECRET_KEY 값들. 공격자가 이미 알고 있다고 봐야 하며,
# 이 값으로 서명하면 누구나 임의 사용자의 JWT 를 위조할 수 있다.
_PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "dev-secret-key-change-in-production",
        "change-this-to-a-random-secret",
    }
)

_SECRET_KEY_HOWTO = "python -c \"import secrets; print(secrets.token_urlsafe(48))\" 로 생성해 SECRET_KEY 에 넣으세요."


def assert_secret_key_configured() -> None:
    """기동 시점에 SECRET_KEY 를 검증한다 — 통과 못 하면 서버가 뜨지 않는다.

    DEBUG 와 독립적인 두 번째 방어선이다. 운영 환경변수를 한 줄 빠뜨렸을 때
    "조용히 위조 가능한 상태로 뜨는" 대신 여기서 즉시 죽는 쪽이 낫다.
    """
    key = settings.secret_key.strip()
    if not key:
        raise RuntimeError(f"SECRET_KEY 가 비어 있어 기동할 수 없습니다. {_SECRET_KEY_HOWTO}")
    if key in _PLACEHOLDER_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY 가 예제 플레이스홀더 값 그대로입니다 — 이 값으로는 누구나 "
            f"토큰을 위조할 수 있어 기동을 중단합니다. {_SECRET_KEY_HOWTO}"
        )


assert_secret_key_configured()


if settings.sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.1,
        )
    except Exception as exc:
        print(f"[startup] Sentry 초기화 실패 — 무시하고 계속: {exc!r}", flush=True)


_AUDIO_PURGE_INTERVAL_S = 3600


async def _audio_purge_loop() -> None:
    from app.services.audio_retention import purge_expired_audio

    while True:
        try:
            async with AsyncSessionLocal() as db:
                purged = await purge_expired_audio(db)
            if purged:
                print(f"[audio-retention] purged {purged} expired audio messages", flush=True)
        except Exception as exc:
            print(f"[audio-retention] purge failed: {exc}", flush=True)
        await asyncio.sleep(_AUDIO_PURGE_INTERVAL_S)


_KST = timezone(timedelta(hours=9))


async def _daily_match_notify_loop() -> None:
    """KST 자정마다 새 인연 카드 도착 푸시를 보낸다(매칭 cycle anchor 와 동일 시각)."""
    from app.services.push import send_daily_match_push

    while True:
        now = datetime.now(_KST)
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        await asyncio.sleep((next_midnight - now).total_seconds())
        try:
            async with AsyncSessionLocal() as db:
                sent = await send_daily_match_push(db)
            print(f"[daily-match] 새 인연 알림 발송 대상 {sent}명", flush=True)
        except Exception as exc:
            print(f"[daily-match] 발송 실패: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db
    await init_db()

    print(
        f"[startup] DATABASE_URL={redact_url_credentials(settings.database_url)}",
        flush=True,
    )
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))
            print(f"[startup] knowledge_chunks rows: {result.scalar_one()}", flush=True)
    except Exception as exc:
        print(f"[startup] knowledge_chunks count failed: {exc}", flush=True)

    purge_task = asyncio.create_task(_audio_purge_loop())
    daily_match_task = asyncio.create_task(_daily_match_notify_loop())

    try:
        yield
    finally:
        purge_task.cancel()
        daily_match_task.cancel()
        from app.services.cache import close_cache
        await close_cache()
        from app.database import engine
        await engine.dispose()


app = FastAPI(
    title="Jamidusu API",
    description="Saju-based matchmaking service",
    version="0.1.0",
    lifespan=lifespan,
)

# 요청 사용자 컨텍스트를 요청마다 초기화한다(DB 세션 변수의 출처 — docs/db-roles.md §6.1).
app.add_middleware(CurrentUserContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 브라우저·WebView 는 노출을 허락한 응답 헤더만 JS 에 넘긴다. 갱신 토큰과
    # 재인증 요구 표식은 클라이언트가 반드시 읽어야 한다(core/deps.py).
    expose_headers=[REAUTH_REQUIRED_HEADER, REFRESHED_TOKEN_HEADER],
)

_DB_BUSY_RETRY_AFTER_S = 5


@app.exception_handler(PoolTimeoutError)
async def _db_pool_exhausted(request: Request, exc: PoolTimeoutError) -> JSONResponse:
    """커넥션 풀 고갈을 500 이 아니라 503 으로 알린다.

    500 은 "서버가 고장났다"라서 클라이언트가 재시도하지 않는다. 풀 고갈은 일시적
    혼잡이므로 503 + Retry-After 로 "잠시 후 다시 오라"고 알려야 한다.
    예외 메시지에는 접속 정보가 섞일 수 있어 응답에 싣지 않는다.
    """
    print(f"[db-pool] 커넥션 풀 고갈: {request.method} {request.url.path}", flush=True)
    return JSONResponse(
        status_code=503,
        content={"detail": "서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."},
        headers={"Retry-After": str(_DB_BUSY_RETRY_AFTER_S)},
    )


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(saju.router, prefix="/saju", tags=["saju"])
app.include_router(compatibility.router, prefix="/compatibility", tags=["compatibility"])
app.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
app.include_router(matching.router, prefix="/matches", tags=["matches"])
app.include_router(stats.router, prefix="/stats", tags=["stats"])


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok"}


@app.get("/health/db", tags=["system"])
async def health_db():
    if not settings.debug:
        raise HTTPException(status_code=404)
    async with AsyncSessionLocal() as db:
        total = (
            await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))
        ).scalar_one()
        by_source = (
            await db.execute(
                text(
                    "SELECT source_type, source_title, COUNT(*) "
                    "FROM knowledge_chunks GROUP BY source_type, source_title"
                )
            )
        ).all()
    return {
        "knowledge_chunks_total": total,
        "by_source": [
            {"source_type": st, "source_title": title, "count": cnt}
            for st, title, cnt in by_source
        ],
    }
