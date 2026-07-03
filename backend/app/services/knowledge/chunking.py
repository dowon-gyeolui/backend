"""결정론적 텍스트 청커 — 단락/문장 경계 분할 후 짧은 청크 병합."""

from __future__ import annotations

import re

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+")

_MERGE_OVERSHOOT = 1.3


def chunk_text(
    text: str,
    max_chars: int = 500,
    min_chars: int = 80,
) -> list[str]:
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
    if not chunks:
        return chunks

    max_soft = int(max_chars * _MERGE_OVERSHOOT)
    out: list[str] = []
    i = 0

    while i < len(chunks):
        current = chunks[i]

        while len(current) < min_chars and i + 1 < len(chunks):
            merged = f"{current}\n\n{chunks[i + 1]}"
            if len(merged) > max_soft:
                break
            current = merged
            i += 1

        if len(current) < min_chars and out:
            merged = f"{out[-1]}\n\n{current}"
            if len(merged) <= max_soft:
                out[-1] = merged
                i += 1
                continue

        out.append(current)
        i += 1

    return out
