"""LLM-based interpretation layer.

Role: summarize retrieved classical passages into a concise Korean
explanation of the user's saju. The LLM is a FORMATTER, never a source
of truth — it must only use the provided source passages as grounding.

Contract:
  - Input: saju result + list of retrieved knowledge chunks (with citations)
  - Output: 2~3 Korean sentences, or None on failure
  - Never invents content beyond the retrieved passages
  - Never makes deterministic predictions (건강/재물/수명 단정 금지)

Cost: ~$0.001 per call at gpt-4o-mini prices (2~4 chunks × 500 tokens).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from app.schemas.saju import SajuResponse

# Default model — kept cheap for MVP. Override via env.
_MODEL = os.environ.get("OPENAI_INTERPRET_MODEL", "gpt-4o-mini")
_MAX_OUTPUT_TOKENS = 400


_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자와 원전 문헌의 관련 구절을 바탕으로 "
    "간결한 한국어 해석을 작성하는 도우미입니다.\n"
    "\n"
    "반드시 지켜야 할 규칙:\n"
    "- 반드시 아래 '검색된 원전 구절' 내용만을 근거로 사용하십시오.\n"
    "- 원전에 명시되지 않은 내용을 추측하거나 추가로 추론하지 마십시오.\n"
    "- 사용자의 일주는 '[사주 결과]'에 적힌 값 그대로 지칭해야 합니다. "
    "원전 구절에 등장하는 다른 일주(예: 병자·갑자 등)를 사용자의 일주로 혼동해서 지칭하지 마십시오.\n"
    "- 원전 구절이 사용자 일주·오행과 직접 관련되는 부분만 인용하여 설명하십시오.\n"
    "- 건강·수명·재물·배우자에 대한 확정적 예언이나 단정적 예측은 하지 마십시오.\n"
    "- '~할 것이다', '~하게 된다'는 단정 대신 '~경향이 있다', '~로 해석됩니다' 같은 완곡한 표현을 사용하십시오.\n"
    "- 총 2~3 문장, 200자 이내로 작성하십시오.\n"
    "- 결과 이외의 문장(도입부·면책·맺음말·번호 매기기·마크다운)은 금지합니다.\n"
    "- 제공된 원전 구절이 사주와 명확히 관련이 없다면 빈 응답을 반환해도 됩니다."
)


@dataclass
class RetrievedPassage:
    """One classical passage fed to the LLM as grounding."""

    citation: str                  # e.g. "《궁통보감》 - 論甲木 - 三春甲木"
    content: str                   # Korean translation of the classical text


@lru_cache(maxsize=1)
def _client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (required for LLM interpretation).")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai SDK not installed. pip install -r requirements.txt") from exc
    return OpenAI(api_key=api_key)


def _build_user_message(saju: SajuResponse, passages: list[RetrievedPassage]) -> str:
    day_pillar = saju.pillars[2]
    ep = saju.element_profile
    named = [
        ("목", ep.wood), ("화", ep.fire), ("토", ep.earth),
        ("금", ep.metal), ("수", ep.water),
    ]
    dom_name, dom_count = max(named, key=lambda x: x[1])

    parts = [
        "[사주 결과]",
        f"- 일주: {day_pillar.combined} (천간 {day_pillar.stem} · 지지 {day_pillar.branch})",
        f"- 오행 분포: 목 {ep.wood} · 화 {ep.fire} · 토 {ep.earth} · 금 {ep.metal} · 수 {ep.water}",
    ]
    if dom_count > 0:
        parts.append(f"- 주요 오행: {dom_name}")
    parts.append("")
    parts.append("[검색된 원전 구절]")

    for i, p in enumerate(passages, start=1):
        # Truncate very long passages so prompt size stays bounded.
        content = p.content if len(p.content) <= 600 else p.content[:600] + "…"
        parts.append(f"{i}. {p.citation}")
        parts.append(f"   {content}")

    parts.append("")
    parts.append("위 원전 구절만을 근거로, 2~3 문장의 한국어 해석을 작성하십시오.")
    return "\n".join(parts)


def _extract_output_text(resp) -> str:
    """Compatible with multiple openai SDK shapes."""
    direct = getattr(resp, "output_text", None)
    if direct:
        return direct.strip()
    parts: list[str] = []
    for block in getattr(resp, "output", []) or []:
        for content in getattr(block, "content", []) or []:
            piece = getattr(content, "text", None)
            if piece:
                parts.append(piece)
    return "".join(parts).strip()


def generate_saju_interpretation(
    saju: SajuResponse,
    passages: list[RetrievedPassage],
    *,
    model: str = _MODEL,
) -> Optional[str]:
    """Call the LLM to generate a grounded Korean interpretation.

    Returns None if there are no passages or the LLM call fails.
    """
    if not passages:
        return None
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_SYSTEM_PROMPT,
            input=_build_user_message(saju, passages),
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        text = _extract_output_text(resp)
        return text or None
    except Exception:
        # Interpretation is best-effort. Never fail the whole /saju/me call.
        return None
