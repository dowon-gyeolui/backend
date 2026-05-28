"""원전 청크(KnowledgeChunk) 테이블 — RAG 검색의 기본 단위.

사주/자미두수 원전을 청킹해 한 행 = 한 검색 단위로 저장한다.
"이 해석의 출처: 《적천수》 3장 오행론" 처럼 풀이의 근거 인용으로
바로 노출할 수 있도록 source_type / source_title / chapter / section /
chunk_index 등의 위치 메타와 함께 보관한다.

임베딩(embedding)은 JSON 배열로 저장하며, PostgreSQL에서는 JSONB,
SQLite에서는 일반 JSON 으로 자동 매핑된다. 추후 pgvector 도입 시
호출부 변경 없이 컬럼 타입만 교체하면 된다.

content_hash 는 SHA-256 으로 idempotent 재적재를 보장한다.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSONB on PostgreSQL (indexed, faster), plain JSON elsewhere (SQLite).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class KnowledgeChunk(Base):
    """One retrievable passage from a source book.

    Designed so each chunk can be shown as direct evidence
    ("이 해석의 출처: 《적천수》 3장 오행론").
    """

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)

    # --- Source identification ---
    source_type = Column(String(50), nullable=False, index=True)
    # Values: "사주" | "자미두수"
    source_title = Column(String(255), nullable=False)
    source_author = Column(String(100), nullable=True)

    # --- Position within the source ---
    topic = Column(String(100), nullable=True, index=True)
    chapter = Column(String(255), nullable=True)
    section = Column(String(255), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)

    # --- Content ---
    content = Column(Text, nullable=False)
    # Display content (Korean translation when available, else original).
    content_original = Column(Text, nullable=True)
    # Source-language text (e.g. classical Chinese). Preserved for audit/display.
    content_hash = Column(String(64), nullable=False, unique=True, index=True)
    # SHA-256 used for deduplication. For JSONL-ingested chunks this hashes
    # content_original (stable key); for API-ingested chunks it hashes content.
    language = Column(String(10), nullable=False, default="ko")
    tags = Column(_JSON, nullable=True)

    # --- Embedding ---
    embedding = Column(_JSON, nullable=True)
    # Vector as a JSON list of floats (1536 dims for text-embedding-3-small).
    # Python-side cosine similarity at MVP scale. Upgrade path: swap to
    # pgvector Column(Vector(1536)) without changing call sites.
    embedding_model = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_kc_source_type_topic", "source_type", "topic"),
    )
