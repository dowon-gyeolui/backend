"""Render Postgres → Supabase 일회용 데이터 마이그레이션 스크립트."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg

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
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _hide(url: str) -> str:
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
    for typ in ("json", "jsonb"):
        try:
            await conn.set_type_codec(
                typ,
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )
        except asyncpg.exceptions.UndefinedObjectError:
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
    await conn.execute("SELECT setval($1, $2, true)", seq, max_id)


async def migrate_table(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    table: str,
) -> tuple[int, int, int]:
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