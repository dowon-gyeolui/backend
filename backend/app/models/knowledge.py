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
