from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class KnowledgeChunkCreate(BaseModel):
    """Input for manually creating a knowledge chunk (dev ingestion via Swagger)."""

    source_type: str = Field(
        examples=["사주"],
        description="소스 유형: '사주' | '자미두수'",
    )
    source_title: str = Field(examples=["적천수"])
    source_author: Optional[str] = Field(default=None, examples=["유백온"])
    topic: Optional[str] = Field(
        default=None,
        examples=["년주론"],
        description="검색 기본 단위. 예: '년주론', '오행론', '궁합기초'",
    )
    chapter: Optional[str] = Field(default=None, examples=["제3장 오행론"])
    section: Optional[str] = Field(default=None, examples=["목화통명"])
    chunk_index: int = Field(default=0, description="소스 내 순서 (0부터 시작)")
    content: str = Field(examples=["갑목은 동방 목기의 양간으로..."])
    language: str = Field(default="ko", examples=["ko"])
    tags: Optional[list[str]] = Field(default=None, examples=[["갑목", "년주", "목기"]])


class KnowledgeChunkResponse(BaseModel):
    """A single stored knowledge chunk, returned by the API."""

    id: int
    source_type: str
    source_title: str
    source_author: Optional[str] = None
    topic: Optional[str] = None
    chapter: Optional[str] = None
    section: Optional[str] = None
    chunk_index: int
    content: str
    content_original: Optional[str] = None
    content_hash: str
    language: str
    tags: Optional[list[str]] = None
    embedding_model: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeQuery(BaseModel):
    """Retrieval query — keyword + optional filters."""

    query: str = Field(
        examples=["갑목 년주"],
        description="검색 키워드 (내용 또는 주제 대상)",
    )
    source_type: Optional[str] = Field(
        default=None,
        examples=["사주"],
        description="소스 유형으로 필터링 (선택)",
    )
    topic: Optional[str] = Field(
        default=None,
        examples=["년주론"],
        description="주제로 필터링 (선택)",
    )
    language: Optional[str] = Field(default=None, examples=["ko"])
    top_k: int = Field(default=3, ge=1, le=20)


class KnowledgeIngestRequest(BaseModel):
    """Input for POST /knowledge/ingest — raw text that will be chunked."""

    source_type: str = Field(examples=["사주"])
    source_title: str = Field(examples=["적천수"])
    source_author: Optional[str] = Field(default=None, examples=["유백온"])
    topic: Optional[str] = Field(default=None, examples=["오행론"])
    chapter: Optional[str] = Field(default=None, examples=["제3장 오행론"])
    section: Optional[str] = Field(default=None, examples=["목화통명"])
    language: str = Field(default="ko", examples=["ko"])
    tags: Optional[list[str]] = Field(default=None, examples=[["갑목", "오행"]])

    text: str = Field(
        description="청킹할 원문 텍스트. 단락은 빈 줄(\\n\\n)로 구분하세요.",
        examples=[
            "갑목은 동방 목기의 양간이다.\n\n을목은 음목으로, 유연하고 굽히는 성질을 가진다."
        ],
    )
    max_chars: int = Field(default=500, ge=50, le=4000)
    starting_index: int = Field(
        default=0, ge=0,
        description="이미 일부가 적재된 소스에 이어 넣을 때 사용.",
    )


class KnowledgeIngestResponse(BaseModel):
    """Result of an ingestion call."""

    total: int  # total chunks produced by the chunker
    created: int  # newly inserted
    skipped_duplicate: int  # skipped because content_hash already existed
    chunks: list[KnowledgeChunkResponse]


class KnowledgeRetrievalResult(BaseModel):
    """One item in a retrieval response — chunk + source evidence metadata.

    source_citation is the human-readable reference string intended to be
    displayed in the UI as direct evidence (e.g. "《적천수》 3장 - 년주론").

    relevance_score is a placeholder (1.0) until vector similarity is live.
    match_reason will become "vector_similarity" once pgvector is active.
    """

    chunk: KnowledgeChunkResponse
    relevance_score: float = Field(
        ge=0.0, le=1.0,
        description="유사도 점수 (0.0~1.0). 현재는 임시값.",
    )
    match_reason: Literal["keyword_match", "topic_filter", "vector_similarity"]
    source_citation: str
    # e.g. "《적천수》 제3장 오행론 - 목화통명"
    # Shown to the user as: "출처: 《적천수》 제3장 오행론"
