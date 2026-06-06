"""Render Postgres → Supabase 일회용 마이그레이션 스크립트.

전제:
- 양쪽 DB 모두 init_db() 가 한 번 이상 돌아 테이블 스키마가 존재해야 함.
- 대상(Supabase)은 비어 있어도 되고, 일부 데이터가 있어도 됨 (ON CONFLICT DO NOTHING).

사용법 (PowerShell):
    cd c:\\Users\\ympyh\\OneDrive\\Desktop\\jamidusu\\backend\\backend
    .\\.venv\\Scripts\\activate
    $env:RENDER_DATABASE_URL = "postgresql://user:pass@dpg-xxx.singapore-postgres.render.com/dbname"
    python scripts/migrate_render_to_supabase.py
    # 끝나면 RENDER_DATABASE_URL 변수는 셸 종료 시 자동 소멸.

대상(Supabase) URL 은 .env 의 DATABASE_URL 에서 자동 로드.

특성:
- idempotent: 같은 row 가 이미 존재하면 ON CONFLICT DO NOTHING 으로 스킵
- FK 의존성 순서대로 테이블 처리
- 모든 테이블 마이그레이션 후 id sequence 를 max(id) 로 setval
- src/dst 컬럼이 다를 경우 교집합만 migrate (스키마 drift 대비)
- knowledge_chunks 의 embedding(JSON) 필드는 codec 등록으로 round-trip
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg

# FK 부모 → 자식 순서.
TABLES: list[str] = [
    "users",
    "knowledge_chunks",
    "user_photos",
    "chat_threads",
    "messages",
    "daily_matches",
    "user_strikes",
    "reports",
]


def _normalize(url: str) -> str:
    """SQLAlchemy 의 'postgresql+asyncpg://' 를 asyncpg 가 받는 'postgresql://' 로."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _hide(url: str) -> str:
    """로그 출력용 — 비밀번호 마스킹."""
    try:
        scheme, rest = url.split("://", 1)
        userinfo, hostpart = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            return f"{scheme}://{user}:***@{hostpart}"
        return url
    except ValueError:
        return "***"


async def _register_json_codec(conn: asyncpg.Connection) -> None:
    """embedding 같은 JSON 컬럼이 dict/list 로 round-trip 되도록 codec 등록."""
    for typ in ("json", "jsonb"):
        try:
            await conn.set_type_codec(
                typ,
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )
        except asyncpg.exceptions.UndefinedObjectError:
            # 타입이 없는 경우 (예: 일부 변형) — 그냥 스킵
            pass


async def _get_columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    return [r["column_name"] for r in rows]


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')


async def _max_id(conn: asyncpg.Connection, table: str) -> int | None:
    val = await conn.fetchval(f'SELECT MAX(id) FROM "{table}"')
    return int(val) if val is not None else None


async def _setval(conn: asyncpg.Connection, table: str, max_id: int) -> None:
    seq = f"{table}_id_seq"
    # is_called=true → 다음 nextval() 호출 시 max_id + 1 반환.
    await conn.execute("SELECT setval($1, $2, true)", seq, max_id)


async def migrate_table(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    table: str,
) -> tuple[int, int, int]:
    """반환: (source row 수, dest before, dest after)."""
    src_cols = await _get_columns(src, table)
    dst_cols = await _get_columns(dst, table)
    common = [c for c in src_cols if c in dst_cols]

    if not common:
        print(f"  {table:20s}  → 공통 컬럼 없음, 스킵")
        return 0, 0, 0

    src_n = await _count(src, table)
    dst_before = await _count(dst, table)

    if src_n == 0:
        print(f"  {table:20s}  source 0 rows, 스킵")
        return 0, dst_before, dst_before

    quoted = ", ".join(f'"{c}"' for c in common)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(common)))
    insert_sql = (
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
        "ON CONFLICT DO NOTHING"
    )

    rows = await src.fetch(f'SELECT {quoted} FROM "{table}" ORDER BY id')
    async with dst.transaction():
        for r in rows:
            await dst.execute(insert_sql, *[r[c] for c in common])

    # sequence 보정 — id 컬럼이 있는 테이블에 한해.
    if "id" in dst_cols:
        mx = await _max_id(dst, table)
        if mx is not None:
            await _setval(dst, table, mx)

    dst_after = await _count(dst, table)
    delta = dst_after - dst_before
    src_dst_match = "✓" if src_n == dst_after else "·"
    print(
        f"  {table:20s}  src={src_n:5d}  "
        f"dst {dst_before:5d} → {dst_after:5d} (+{delta})  {src_dst_match}"
    )
    return src_n, dst_before, dst_after


async def main() -> int:
    src_url = os.environ.get("RENDER_DATABASE_URL")
    if not src_url:
        print(
            "ERROR: 환경변수 RENDER_DATABASE_URL 이 설정되지 않았습니다.\n"
            "PowerShell 예시:\n"
            "  $env:RENDER_DATABASE_URL = \"postgresql://user:pass@dpg-xxx.region-postgres.render.com/dbname\"",
            file=sys.stderr,
        )
        return 1

    # Supabase URL 은 .env 의 DATABASE_URL.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from app.config import settings

    src_url = _normalize(src_url)
    dst_url = _normalize(settings.database_url)

    print("=== 연결 정보 ===")
    print(f"  SOURCE: {_hide(src_url)}")
    print(f"  DEST  : {_hide(dst_url)}")
    print()

    src = await asyncpg.connect(src_url, ssl="require")
    dst = await asyncpg.connect(dst_url, ssl="require")
    await _register_json_codec(src)
    await _register_json_codec(dst)

    try:
        print("=== 마이그레이션 ===")
        results: list[tuple[str, int, int, int]] = []
        for table in TABLES:
            src_n, before, after = await migrate_table(src, dst, table)
            results.append((table, src_n, before, after))

        print()
        print("=== 결과 요약 ===")
        all_ok = True
        for table, src_n, _before, after in results:
            mark = "✓" if src_n == after else "⚠"
            if src_n != after:
                all_ok = False
            print(f"  {mark} {table:20s}  source={src_n:5d}  dest={after:5d}")

        print()
        if all_ok:
            print("✓ 모든 테이블의 row 수가 일치합니다. 마이그레이션 완료.")
            return 0
        print("⚠ 일부 테이블에서 row 수가 다릅니다. 위 결과 확인 후 재실행 가능.")
        return 2
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))