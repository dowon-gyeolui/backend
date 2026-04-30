from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal
from app.routers import (
    auth,
    chat,
    compatibility,
    knowledge,
    recommendations,
    reports,
    saju,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db
    await init_db()

    # Dev-time sanity check: log the actually-resolved DB URL and the number
    # of knowledge chunks visible to the FastAPI process. If this count is 0
    # but the ingestion script reported more, the two processes are pointing
    # at different SQLite files.
    print(f"[startup] DATABASE_URL={settings.database_url}", flush=True)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))
            print(f"[startup] knowledge_chunks rows: {result.scalar_one()}", flush=True)
    except Exception as exc:
        print(f"[startup] knowledge_chunks count failed: {exc}", flush=True)

    yield


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


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok"}


@app.get("/health/db", tags=["system"])
async def health_db():
    """Dev helper: confirm FastAPI and the ingestion script point at the same DB."""
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
        "database_url": settings.database_url,
        "knowledge_chunks_total": total,
        "by_source": [
            {"source_type": st, "source_title": title, "count": cnt}
            for st, title, cnt in by_source
        ],
    }
