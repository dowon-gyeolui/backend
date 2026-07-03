"""RAG 지식 청크 모델(KnowledgeChunk) — 원전 텍스트 + 임베딩 저장."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

_JSON = JSON().with_variant(JSONB(), "postgresql")

class KnowledgeChunk(Base):

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)

    source_type = Column(String(50), nullable=False, index=True)
    source_title = Column(String(255), nullable=False)
    source_author = Column(String(100), nullable=True)

    topic = Column(String(100), nullable=True, index=True)
    chapter = Column(String(255), nullable=True)
    section = Column(String(255), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)

    content = Column(Text, nullable=False)
    content_original = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False, unique=True, index=True)
    language = Column(String(10), nullable=False, default="ko")
    tags = Column(_JSON, nullable=True)

    embedding = Column(_JSON, nullable=True)
    embedding_model = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_kc_source_type_topic", "source_type", "topic"),
    )
