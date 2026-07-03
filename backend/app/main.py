"""FastAPI 앱 엔트리포인트: lifespan(DB 초기화, 음성 정리 루프), CORS, 라우터 등록."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
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


def _redact_db_url(url: str) -> str:
    try:
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        userinfo, hostpart = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            return f"{scheme}://{user}:***@{hostpart}"
        return url
    except ValueError:
        return "***"


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db
    await init_db()

    print(f"[startup] DATABASE_URL={_redact_db_url(settings.database_url)}", flush=True)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))
            print(f"[startup] knowledge_chunks rows: {result.scalar_one()}", flush=True)
    except Exception as exc:
        print(f"[startup] knowledge_chunks count failed: {exc}", flush=True)

    purge_task = asyncio.create_task(_audio_purge_loop())

    try:
        yield
    finally:
        purge_task.cancel()
        from app.database import engine
        await engine.dispose()


app = FastAPI(
    title="Jamidusu API",
    description="Saju-based matchmaking service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
