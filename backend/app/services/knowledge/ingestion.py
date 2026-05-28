"""원전 텍스트 청크 적재 서비스 — 해시 기반 멱등(idempotent) 보장.

ingest_text() 가 단일 진입점이며 다음에서 호출된다.
  - POST /knowledge/ingest (개발용 API)
  - 향후 CLI / 배치 스크립트
  - 향후 파일 리더(txt/md/pdf → 텍스트 → ingest_text)

책임:
  - chunking.chunk_text() 로 청킹
  - 각 청크의 SHA-256 content_hash 계산
  - 이미 존재하는 해시는 스킵(재적재 안전)
  - 새 청크는 전체 메타데이터와 함께 읽기 순서대로 적재

범위 밖(향후):
  - 파일 포맷 리더 — 이 함수에 추출된 텍스트를 넘겨 감싸기만 하면 됨
  - 임베딩 생성 — 커밋 후 별도로 embedding 컬럼을 채움
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
