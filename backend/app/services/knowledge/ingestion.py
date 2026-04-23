"""Knowledge ingestion service.

ingest_text() is the single entrypoint used by:
  - POST /knowledge/ingest (dev API endpoint)
  - future CLI / batch scripts
  - future file readers (txt / md / pdf → text → ingest_text)

Responsibilities:
  - Chunk the input via chunking.chunk_text()
  - Compute SHA-256 content_hash for each chunk
  - Skip chunks whose hash already exists (idempotent re-ingestion)
  - Persist new chunks with full metadata in reading order

Out of scope (marked for future):
  - File format readers — wrap this function and pass extracted text
  - Embedding generation — run after commit, populate embedding column
"""

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk
from app.services.knowledge.chunking import chunk_text


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class IngestResult:
    total: int                              # total pieces produced by the chunker
    created: int                            # newly inserted chunks
    skipped_duplicate: int                  # pieces whose content_hash already existed
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    # `chunks` is in reading order and contains BOTH newly created and existing
    # duplicate chunks, so the caller sees the full set backing this ingest call.


async def ingest_text(
    *,
    source_type: str,
    source_title: str,
    text: str,
    db: AsyncSession,
    source_author: Optional[str] = None,
    topic: Optional[str] = None,
    chapter: Optional[str] = None,
    section: Optional[str] = None,
    language: str = "ko",
    tags: Optional[list[str]] = None,
    max_chars: int = 500,
    starting_index: int = 0,
) -> IngestResult:
    """Chunk, hash, deduplicate, and persist. Commits once at the end."""
    pieces = chunk_text(text, max_chars=max_chars)

    all_chunks: list[KnowledgeChunk] = []
    new_chunks: list[KnowledgeChunk] = []
    skipped = 0

    for offset, piece in enumerate(pieces):
        content_hash = hash_content(piece)

        existing = (
            await db.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.content_hash == content_hash)
            )
        ).scalar_one_or_none()

        if existing is not None:
            all_chunks.append(existing)
            skipped += 1
            continue

        chunk = KnowledgeChunk(
            source_type=source_type,
            source_title=source_title,
            source_author=source_author,
            topic=topic,
            chapter=chapter,
            section=section,
            chunk_index=starting_index + offset,
            content=piece,
            content_hash=content_hash,
            language=language,
            tags=tags,
        )
        db.add(chunk)
        new_chunks.append(chunk)
        all_chunks.append(chunk)

    await db.commit()
    for chunk in new_chunks:
        await db.refresh(chunk)

    return IngestResult(
        total=len(pieces),
        created=len(new_chunks),
        skipped_duplicate=skipped,
        chunks=all_chunks,
    )
