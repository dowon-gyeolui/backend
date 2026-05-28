"""결정론적 텍스트 청커 — 짧은 청크 병합 후처리 포함.

전략(순서대로):
  1. 빈 줄 기준 단락 분리.
  2. 짧은 단락을 max_chars 이하로 그리디 병합.
  3. 단락 하나가 max_chars 를 넘으면 문장 경계로 분할
     (`.` `!` `?` `。` `？` `！` + 공백).
  4. 한 문장이 여전히 max_chars 보다 길면 max_chars 단위로 강제 분할.
  5. min_chars 미만의 조각은 max_chars * 1.3 까지 허용하며 이웃과 병합.

특성:
  - 결정론적: 같은 입력 → 같은 출력.
  - 순서 보존: 본문 읽기 순서대로 반환.
  - 비중복: 청크 간 오버랩 없음.
"""

from __future__ import annotations

import re

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+")

_MERGE_OVERSHOOT = 1.3  # allow 30% overshoot of max_chars when merging fragments


def chunk_text(
    text: str,
    max_chars: int = 500,
    min_chars: int = 80,
) -> list[str]:
    """Split text into chunk-sized pieces, paragraph-first, then merge shorts."""
    if not text or not text.strip():
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if min_chars < 0:
        raise ValueError("min_chars must be >= 0")
    if min_chars > max_chars:
        raise ValueError("min_chars must be <= max_chars")

    chunks = _split_paragraphs(text, max_chars)
    if min_chars > 0:
        chunks = _merge_short(chunks, min_chars=min_chars, max_chars=max_chars)
    return chunks


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]

    chunks: list[str] = []
    buffer = ""

    for para in paragraphs:
        if len(para) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_split_long(para, max_chars))
            continue

        if not buffer:
            buffer = para
        elif len(buffer) + len(para) + 2 <= max_chars:
            buffer = f"{buffer}\n\n{para}"
        else:
            chunks.append(buffer)
            buffer = para

    if buffer:
        chunks.append(buffer)
    return chunks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
    if not sentences:
        sentences = [paragraph]

    out: list[str] = []
    buffer = ""

    for sent in sentences:
        if len(sent) > max_chars:
            if buffer:
                out.append(buffer)
                buffer = ""
            for i in range(0, len(sent), max_chars):
                out.append(sent[i:i + max_chars])
            continue

        if not buffer:
            buffer = sent
        elif len(buffer) + len(sent) + 1 <= max_chars:
            buffer = f"{buffer} {sent}"
        else:
            out.append(buffer)
            buffer = sent

    if buffer:
        out.append(buffer)
    return out


def _merge_short(
    chunks: list[str],
    *,
    min_chars: int,
    max_chars: int,
) -> list[str]:
    """Fold chunks shorter than min_chars into a neighbor where feasible."""
    if not chunks:
        return chunks

    max_soft = int(max_chars * _MERGE_OVERSHOOT)
    out: list[str] = []
    i = 0

    while i < len(chunks):
        current = chunks[i]

        # Forward-merge as long as current is too short and next exists.
        while len(current) < min_chars and i + 1 < len(chunks):
            merged = f"{current}\n\n{chunks[i + 1]}"
            if len(merged) > max_soft:
                break
            current = merged
            i += 1

        # Still too short (e.g. last chunk) — fold back into previous.
        if len(current) < min_chars and out:
            merged = f"{out[-1]}\n\n{current}"
            if len(merged) <= max_soft:
                out[-1] = merged
                i += 1
                continue

        out.append(current)
        i += 1

    return out
