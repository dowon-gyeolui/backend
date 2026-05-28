"""텍스트 임베딩 서비스 — OpenAI text-embedding-3-small (1536 차원).

진입점:
  embed_text(text)              — 단일 벡터(retrieval 쿼리에서 사용)
  embed_texts(texts)            — 배치 벡터(ingestion 에서 사용)
  build_chunk_embedding_input() — 청크 임베딩 입력 표준 포매터
                                  (원문 + 한국어 + 메타데이터)

ingestion 과 retrieval 은 반드시 동일한 포매터(또는 정규화)를 거쳐야
쿼리 벡터와 청크 벡터가 같은 의미 공간에 살게 된다. OPENAI_API_KEY
환경변수가 필요하다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


@lru_cache(maxsize=1)
def _client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Required for embedding generation."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai SDK not installed. Run: pip install -r requirements.txt"
        ) from exc
    return OpenAI(api_key=api_key)


def embed_text(text: str) -> list[float]:
    resp = _client().embeddings.create(model=EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _client().embeddings.create(model=EMBEDDING_MODEL, input=texts)
    # API preserves input order.
    return [item.embedding for item in resp.data]


def build_chunk_embedding_input(
    *,
    content_original: Optional[str] = None,
    content_korean: Optional[str] = None,
    source_title: Optional[str] = None,
    chapter: Optional[str] = None,
    topic: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Concatenate original + translation + metadata into one embedding input.

    Including both languages improves cross-lingual retrieval.
    The trailing metadata line gives the embedding model source/topic signal.
    """
    parts: list[str] = []
    if content_original and content_original.strip():
        parts.append(content_original.strip())
    if content_korean and content_korean.strip():
        parts.append(content_korean.strip())

    meta_bits: list[str] = []
    if source_title:
        meta_bits.append(f"출처: 《{source_title}》")
    if chapter:
        meta_bits.append(f"장: {chapter}")
    if topic:
        meta_bits.append(f"주제: {topic}")
    if tags:
        meta_bits.append(f"태그: {', '.join(tags)}")
    if meta_bits:
        parts.append(" / ".join(meta_bits))

    return "\n\n".join(parts)
