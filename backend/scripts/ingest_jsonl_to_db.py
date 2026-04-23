"""Ingest a knowledge JSONL file into the DB with embeddings.

Usage
-----
    export OPENAI_API_KEY=sk-...
    python scripts/ingest_jsonl_to_db.py \
        --input data/processed/적천수.jsonl \
        [--batch-size 16]

Behavior
--------
- Reads JSONL rows produced by build_knowledge_jsonl.py / translate_chunks.py
- Skips any row whose content_hash already exists (idempotent re-ingestion)
- Embeds new rows in batches via text-embedding-3-small
- Inserts KnowledgeChunk rows with content_original + embedding
- Prints a final summary: total / created / skipped

Notes
-----
- `content` gets the Korean translation if present, else the original.
- `content_original` always preserves the source-language text.
- `content_hash` is taken verbatim from the JSONL row (stable across re-translate).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/ingest_jsonl_to_db.py` to import from `app.*`
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file, load_jsonl  # noqa: E402

# Load backend/.env before importing anything that reads env vars (e.g. DB URL,
# OPENAI_API_KEY). pydantic-settings only loads .env inside the FastAPI app.
load_env_file(_BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal, init_db  # noqa: E402
from app.models.knowledge import KnowledgeChunk  # noqa: E402
from app.services.knowledge.embedding import (  # noqa: E402
    EMBEDDING_MODEL,
    build_chunk_embedding_input,
    embed_texts,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--input", required=True, help="Path to a JSONL file.")
    p.add_argument("--batch-size", type=int, default=16,
                   help="How many chunks to embed per OpenAI API call.")
    return p.parse_args()


def _row_to_chunk(row: dict, vector: list[float]) -> KnowledgeChunk:
    content_display = (
        row.get("content_korean")
        or row.get("content_original")
        or ""
    )
    return KnowledgeChunk(
        source_type=row.get("source_type"),
        source_title=row.get("source_title"),
        source_author=row.get("source_author"),
        topic=row.get("topic"),
        chapter=row.get("chapter"),
        section=row.get("section"),
        chunk_index=row.get("chunk_index", 0),
        content=content_display,
        content_original=row.get("content_original"),
        content_hash=row["content_hash"],
        language=row.get("language") or "ko",
        tags=row.get("tags"),
        embedding=vector,
        embedding_model=EMBEDDING_MODEL,
    )


async def _run(input_path: Path, batch_size: int) -> int:
    await init_db()

    rows = list(load_jsonl(input_path))
    total = len(rows)
    created = 0
    skipped = 0
    failed = 0

    async with AsyncSessionLocal() as db:
        # Split into (insert) vs (skip-duplicate) by checking content_hash
        rows_to_insert: list[dict] = []
        for row in rows:
            h = row.get("content_hash")
            if not h:
                failed += 1
                continue
            existing = (
                await db.execute(
                    select(KnowledgeChunk.id).where(KnowledgeChunk.content_hash == h)
                )
            ).scalar_one_or_none()
            if existing:
                skipped += 1
            else:
                rows_to_insert.append(row)

        # Batch-embed, insert, commit per batch (progress is persisted)
        for i in range(0, len(rows_to_insert), batch_size):
            batch = rows_to_insert[i : i + batch_size]
            inputs = [
                build_chunk_embedding_input(
                    content_original=r.get("content_original"),
                    content_korean=r.get("content_korean"),
                    source_title=r.get("source_title"),
                    chapter=r.get("chapter"),
                    topic=r.get("topic"),
                    tags=r.get("tags"),
                )
                for r in batch
            ]
            try:
                vectors = embed_texts(inputs)
            except Exception as exc:
                print(f"  embedding batch failed ({len(batch)} rows): {exc}",
                      file=sys.stderr)
                failed += len(batch)
                continue

            for row, vec in zip(batch, vectors):
                db.add(_row_to_chunk(row, vec))
                created += 1

            await db.commit()
            print(f"  committed batch: {min(i + batch_size, len(rows_to_insert))}"
                  f" / {len(rows_to_insert)} new rows")

    print(f"total={total}, created={created}, skipped={skipped}, failed={failed}")
    return 0 if failed == 0 else 2


def main() -> int:
    args = _parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1
    return asyncio.run(_run(input_path, args.batch_size))


if __name__ == "__main__":
    raise SystemExit(main())
