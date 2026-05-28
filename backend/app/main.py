"""FastAPI 앱 진입점.

- lifespan: 기동 시 init_db() 호출 + 지식 청크 카운트 로그
- CORS: settings.cors_origins 기반 화이트리스트
- 라우터: auth / users / saju / compatibility / recommendations /
  knowledge / chat / reports
- /health, /health/db: 운영 점검용 ping/DB 확인 엔드포인트
"""

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
    recommendations,
    reports,
    saju,
    users,
)


def _redact_db_url(url: str) -> str:
    """connection 문자열에서 비밀번호만 *** 로 마스킹.

    형식 가정: scheme://user:password@host:port/dbname.
    user:password@ 부분이 없으면 그대로 반환.
    """
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import init_db
    await init_db()

    # 시동 시 sanity check — knowledge_chunks 카운트를 찍어 동일 DB 에 붙었는지 확인.
    # DATABASE_URL 은 비밀번호를 마스킹해 로그에 남긴다. 절대 평문으로 찍지 말 것.
    print(f"[startup] DATABASE_URL={_redact_db_url(settings.database_url)}", flush=True)
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
    """지식 청크 인제스천 상태 확인용 헬스 체크.

    debug=True 일 때만 활성화. production 에서는 404 로 응답해 정보 노출을 막는다.
    이전 버전은 응답에 ``database_url`` 을 그대로 실어 비밀번호가 인터넷에
    공개됐었음 — 이 엔드포인트의 응답은 절대 connection 문자열을 포함하지 않는다.
    """
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
