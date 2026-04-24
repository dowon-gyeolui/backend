"""Temporary end-to-end flow test — exercises every MVP endpoint in-process.

Uses httpx.AsyncClient with ASGITransport so no external uvicorn is needed.
Prints condensed Korean output per step. Safe to delete after verification.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file  # noqa: E402
load_env_file(_BACKEND_ROOT)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.database import AsyncSessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402


def _section(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def _dump(label: str, status: int, body: dict | list, max_lines: int = 20):
    print(f"[{status}] {label}")
    s = json.dumps(body, ensure_ascii=False, indent=2)
    lines = s.splitlines()
    for line in lines[:max_lines]:
        print("  ", line)
    if len(lines) > max_lines:
        print(f"   ... ({len(lines) - max_lines} more lines)")


async def _flip_is_paid(dev_user_id: int, paid: bool) -> None:
    """Dev shortcut: upgrade/downgrade a user's paid flag directly."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(User).where(User.kakao_id == f"dev_{dev_user_id}"))
        ).scalar_one_or_none()
        if row is None:
            print(f"  (user dev_{dev_user_id} not found)")
            return
        row.is_paid = paid
        await db.commit()
    print(f"  ※ dev_{dev_user_id} is_paid={paid} 설정 완료")


async def main() -> int:
    # Initialize DB (lifespan normally does this; ASGITransport skips lifespan).
    await init_db()

    # Startup-log simulation (matches what uvicorn would show).
    from app.config import settings
    print(f"[startup] DATABASE_URL={settings.database_url}")
    async with AsyncSessionLocal() as db:
        count = (await db.execute(text("SELECT COUNT(*) FROM knowledge_chunks"))).scalar_one()
        print(f"[startup] knowledge_chunks rows: {count}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ---------- USER 1 setup ----------
        _section("1) /users/me/birth-data (user 1)")
        r = await client.post(
            "/users/me/birth-data",
            headers={"X-Dev-User-Id": "1"},
            json={
                "birth_date": "1990-05-15",
                "birth_time": "14:30",
                "calendar_type": "solar",
                "is_leap_month": False,
                "gender": "male",
            },
        )
        _dump("POST /users/me/birth-data", r.status_code, r.json())

        _section("2) /users/me/profile (user 1)")
        r = await client.patch(
            "/users/me/profile",
            headers={"X-Dev-User-Id": "1"},
            json={"nickname": "민준", "photo_url": "https://example.com/minjun.jpg"},
        )
        _dump("PATCH /users/me/profile", r.status_code, r.json())

        # ---------- SAJU ----------
        _section("3) /saju/me (user 1) — RAG + LLM 해석")
        r = await client.get("/saju/me", headers={"X-Dev-User-Id": "1"})
        body = r.json()
        _dump("GET /saju/me", r.status_code, {
            "pillars": [p["combined"] for p in body.get("pillars", [])],
            "element_profile": body.get("element_profile"),
            "interpretation_status": body.get("interpretation_status"),
            "interpretation_sources": body.get("interpretation_sources"),
            "interpretation": body.get("interpretation"),
        })

        # ---------- PRE-MATCH RECOMMENDATION (무료) ----------
        _section("4) /recommendations/me (user 1) — 사전 추천 무료")
        r = await client.get("/recommendations/me", headers={"X-Dev-User-Id": "1"})
        _dump("GET /recommendations/me", r.status_code, r.json())

        # ---------- USER 2, 3 setup (매칭 후보) ----------
        _section("5) user 2, 3 셋업 (매칭 후보 생성)")
        for uid, payload in [
            ("2", {"birth_date": "1992-08-20", "birth_time": "09:15",
                   "calendar_type": "solar", "is_leap_month": False, "gender": "female"}),
            ("3", {"birth_date": "1988-12-03", "birth_time": "23:45",
                   "calendar_type": "solar", "is_leap_month": False, "gender": "female"}),
        ]:
            await client.post("/users/me/birth-data", headers={"X-Dev-User-Id": uid}, json=payload)
            await client.patch("/users/me/profile",
                               headers={"X-Dev-User-Id": uid},
                               json={"nickname": {"2": "수아", "3": "지민"}[uid]})
        print("  user 2(수아), 3(지민) 셋업 완료")

        # ---------- MATCHES (무료 블라인드) ----------
        _section("6) /compatibility/matches (user 1, 무료 → 블라인드)")
        r = await client.get("/compatibility/matches?top_k=5", headers={"X-Dev-User-Id": "1"})
        _dump("GET /compatibility/matches", r.status_code, r.json())

        # ---------- COMPATIBILITY SCORE ----------
        _section("7) /compatibility/score/{id} (user 1 ↔ user 2)")
        # Look up user 2's actual id (auto-assigned)
        async with AsyncSessionLocal() as db:
            user2 = (await db.execute(select(User).where(User.kakao_id == "dev_2"))).scalar_one()
            target_id = user2.id
        r = await client.get(f"/compatibility/score/{target_id}", headers={"X-Dev-User-Id": "1"})
        _dump(f"GET /compatibility/score/{target_id}", r.status_code, r.json())

        # ---------- UPGRADE user 1 to paid ----------
        _section("8) user 1 유료 전환")
        await _flip_is_paid(1, True)

        # ---------- MATCHES (유료 언블라인드) ----------
        _section("9) /compatibility/matches (user 1, 유료 → 언블라인드)")
        r = await client.get("/compatibility/matches?top_k=5", headers={"X-Dev-User-Id": "1"})
        _dump("GET /compatibility/matches", r.status_code, r.json())

        # ---------- PAIR RECOMMENDATION (유료) ----------
        _section(f"10) /recommendations/pair/{target_id} (user 1 → user 2, 유료)")
        r = await client.get(
            f"/recommendations/pair/{target_id}",
            headers={"X-Dev-User-Id": "1"},
        )
        _dump(f"GET /recommendations/pair/{target_id}", r.status_code, r.json())

        # ---------- HEALTH CHECK ----------
        _section("11) /health/db")
        r = await client.get("/health/db")
        _dump("GET /health/db", r.status_code, r.json())

    print("\n✓ end-to-end 플로우 전 구간 호출 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
