from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.knowledge import KnowledgeChunk
from app.schemas.knowledge import (
    KnowledgeChunkCreate,
    KnowledgeChunkResponse,
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
)
from app.services.knowledge import ingestion, retrieval

router = APIRouter()


@router.post(
    "/chunks",
    response_model=KnowledgeChunkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="원전 청크 단일 추가 (개발/테스트용)",
    description=(
        "이미 청킹된 텍스트 한 조각을 그대로 저장합니다. "
        "동일 content_hash가 이미 존재하면 기존 청크를 반환합니다."
    ),
)
async def create_chunk(
    data: KnowledgeChunkCreate,
    db: AsyncSession = Depends(get_db),
):
    content_hash = ingestion.hash_content(data.content)

    existing = (
        await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.content_hash == content_hash)
        )
    ).scalar_one_or_none()

    if existing:
        return existing

    chunk = KnowledgeChunk(
        source_type=data.source_type,
        source_title=data.source_title,
        source_author=data.source_author,
        topic=data.topic,
        chapter=data.chapter,
        section=data.section,
        chunk_index=data.chunk_index,
        content=data.content,
        content_hash=content_hash,
        language=data.language,
        tags=data.tags,
    )
    db.add(chunk)
    await db.commit()
    await db.refresh(chunk)
    return chunk


@router.post(
    "/ingest",
    response_model=KnowledgeIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="원문 텍스트 일괄 적재 (청킹 포함)",
    description=(
        "원문 텍스트를 단락 기준으로 청킹하여 일괄 저장합니다. "
        "content_hash가 겹치는 청크는 건너뜁니다."
    ),
)
async def ingest_knowledge(
    data: KnowledgeIngestRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await ingestion.ingest_text(
        source_type=data.source_type,
        source_title=data.source_title,
        text=data.text,
        db=db,
        source_author=data.source_author,
        topic=data.topic,
        chapter=data.chapter,
        section=data.section,
        language=data.language,
        tags=data.tags,
        max_chars=data.max_chars,
        starting_index=data.starting_index,
    )
    return KnowledgeIngestResponse(
        total=result.total,
        created=result.created,
        skipped_duplicate=result.skipped_duplicate,
        chunks=[KnowledgeChunkResponse.model_validate(c) for c in result.chunks],
    )


@router.post(
    "/retrieve",
    response_model=list[KnowledgeRetrievalResult],
    summary="원전 청크 검색",
    description=(
        "키워드와 필터로 저장된 원전 청크를 검색합니다. "
        "각 결과는 source_citation 필드로 출처 정보를 포함합니다. "
        "저장된 청크가 없으면 임시 placeholder를 반환합니다."
    ),
)
async def retrieve_knowledge(
    query: KnowledgeQuery,
    db: AsyncSession = Depends(get_db),
):
    return await retrieval.retrieve(query, db)
