"""지식 검색 — 벡터 유사도 우선, 키워드 폴백.

호출당 결정 트리:
  1. 기본 필터(source_type / topic / language)로 후보 집합 구성.
  2. 후보 중 임베딩 보유 청크가 하나라도 있으면:
       - OpenAI 로 쿼리 임베딩 생성
       - 파이썬에서 cosine similarity 계산
       - top_k 반환, match_reason = "vector_similarity"
  3. 그 외엔 content/topic 키워드 LIKE 검색:
       - top_k 반환, match_reason = "keyword_match"
  4. 그것도 없으면 placeholder 결과(Swagger 가 빈 DB 에서도 동작).

불변:
  모든 결과는 source_citation 을 포함해 호출자가 출처를 표시할 수 있다.

향후 업그레이드(호출부 변경 없이):
  2단계가 `ORDER BY embedding <=> query_vec LIMIT top_k` 로 바뀜
  (pgvector 도입 시).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk
from app.schemas.knowledge import (
    KnowledgeChunkResponse,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
)
from app.services.knowledge.embedding import embed_text


def _build_citation(chunk: KnowledgeChunk) -> str:
    # Containment order: book → chapter → section. `topic` is a cross-book
    # retrieval category so it appears last when present.
    parts = [f"《{chunk.source_title}》"]
    if chunk.chapter:
        parts.append(chunk.chapter)
    if chunk.section:
        parts.append(chunk.section)
    if chunk.topic:
        parts.append(chunk.topic)
    return " - ".join(parts)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _apply_filters(stmt, query: KnowledgeQuery):
    if query.source_type:
        stmt = stmt.where(KnowledgeChunk.source_type == query.source_type)
    if query.topic:
        stmt = stmt.where(KnowledgeChunk.topic == query.topic)
    if query.language:
        stmt = stmt.where(KnowledgeChunk.language == query.language)
    return stmt


async def retrieve(
    query: KnowledgeQuery,
    db: AsyncSession,
) -> list[KnowledgeRetrievalResult]:
    # --- 1) Vector path -----------------------------------------------
    vector_stmt = _apply_filters(
        select(KnowledgeChunk).where(KnowledgeChunk.embedding.is_not(None)),
        query,
    )
    vector_rows = (await db.execute(vector_stmt)).scalars().all()

    if vector_rows:
        try:
            query_vec = embed_text(query.query)
        except Exception:
            query_vec = None

        if query_vec is not None:
            scored: list[tuple[KnowledgeChunk, float]] = []
            for chunk in vector_rows:
                if not chunk.embedding:
                    continue
                scored.append((chunk, _cosine(query_vec, chunk.embedding)))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            top = scored[: query.top_k]
            return [
                KnowledgeRetrievalResult(
                    chunk=KnowledgeChunkResponse.model_validate(chunk),
                    relevance_score=max(0.0, min(1.0, score)),
                    match_reason="vector_similarity",
                    source_citation=_build_citation(chunk),
                )
                for chunk, score in top
            ]

    # --- 2) Keyword fallback ------------------------------------------
    keyword_stmt = _apply_filters(select(KnowledgeChunk), query)
    keyword = query.query.strip()
    if keyword:
        pattern = f"%{keyword}%"
        keyword_stmt = keyword_stmt.where(
            or_(
                KnowledgeChunk.content.ilike(pattern),
                KnowledgeChunk.topic.ilike(pattern),
            )
        )
    keyword_stmt = keyword_stmt.order_by(KnowledgeChunk.chunk_index).limit(query.top_k)
    rows = (await db.execute(keyword_stmt)).scalars().all()

    if rows:
        return [
            KnowledgeRetrievalResult(
                chunk=KnowledgeChunkResponse.model_validate(chunk),
                relevance_score=0.5,
                match_reason="keyword_match",
                source_citation=_build_citation(chunk),
            )
            for chunk in rows
        ]

    # --- 3) Empty-DB placeholder --------------------------------------
    return _placeholder_results(query)


def _placeholder_results(query: KnowledgeQuery) -> list[KnowledgeRetrievalResult]:
    return [
        KnowledgeRetrievalResult(
            chunk=KnowledgeChunkResponse(
                id=0,
                source_type=query.source_type or "사주",
                source_title="(예시) 적천수",
                source_author=None,
                topic=query.topic or query.query or "미분류",
                chapter=None,
                section=None,
                chunk_index=0,
                content=(
                    "(임시) 관련 원전 내용이 여기에 표시됩니다. "
                    "실제 원전 데이터를 적재(ingest)하면 이 항목은 실제 검색 결과로 대체됩니다."
                ),
                content_original=None,
                content_hash="placeholder",
                language="ko",
                tags=["placeholder"],
                embedding_model=None,
                created_at=datetime.now(timezone.utc),
            ),
            relevance_score=0.0,
            match_reason="keyword_match",
            source_citation="(임시) 실제 원전 미적재 상태",
        )
    ]
