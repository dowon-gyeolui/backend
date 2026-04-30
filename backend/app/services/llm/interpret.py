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

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from app.schemas.saju import SajuResponse

# Default model — kept cheap for MVP. Override via env.
_MODEL = os.environ.get("OPENAI_INTERPRET_MODEL", "gpt-4o-mini")
_MAX_OUTPUT_TOKENS = 400


_PLAIN_KOREAN_RULES = (
    "- 반드시 한국어로만 답변하십시오. 영어 단어·문장 사용 금지.\n"
    "- 일반인이 이해할 수 있는 쉬운 표현을 쓰십시오. 어려운 한자어를 단독으로 쓰지 말고, "
    "꼭 필요하면 괄호 안에 풀이를 함께 적으십시오. 예: '관성(직장운)', '비견(친구운)'.\n"
    "- 사주 전문 용어(인성·식상·관살·재성 등)는 한 번이라도 등장하면 즉시 풀이를 덧붙이십시오. "
    "예: '인성(부모·공부 운)'.\n"
    "- 한자어를 풀어쓰기 어려운 경우(예: 정인, 식신)는 평이한 한국어로 의미만 전달하십시오.\n"
)


_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자와 원전 문헌의 관련 구절을 바탕으로 "
    "간결한 한국어 해석을 작성하는 도우미입니다.\n"
    "\n"
    "반드시 지켜야 할 규칙:\n"
    + _PLAIN_KOREAN_RULES +
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


# --- Pair recommendation prompt (post-match) ------------------------

_PAIR_SYSTEM_PROMPT = (
    "당신은 두 사용자의 사주 궁합을 분석하여 대화 주제와 데이트 팁을 "
    "한국어로 제안하는 도우미입니다.\n"
    "\n"
    "반드시 지킬 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- '검색된 원전 구절' 내용만 근거로 사용하고, 원전에 없는 내용은 추측하지 마십시오.\n"
    "- 건강·수명·파탄·불륜 등 확정적 예언은 금지합니다.\n"
    "- '~할 것이다' 대신 '~경향이 있습니다', '~로 해석됩니다' 같은 완곡한 표현을 사용하십시오.\n"
    "- 각 항목은 간결한 한국어 한 문장 (20~60자) 으로 작성하십시오.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "strengths": ["...", "...", "..."],              // 이 커플의 강점 1~3개\n'
    '  "cautions": ["...", "..."],                       // 유의점 1~3개\n'
    '  "conversation_starters": ["...", "...", "..."],  // 대화 주제 제안 2~3개\n'
    '  "summary": "2~3 문장의 한국어 요약."\n'
    "}\n"
    "\n"
    "원전 구절과 사주 결과가 명확한 연결점이 없다면 빈 배열과 빈 summary를 "
    "포함한 JSON 을 반환하십시오."
)


def _build_pair_message(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
    passages: list[RetrievedPassage],
) -> str:
    nick_a = user_a_info.get("nickname") or "사용자A"
    nick_b = user_b_info.get("nickname") or "사용자B"
    lines = [
        "[궁합 분석 입력]",
        f"- 궁합 점수: {score} / 100",
        f"- {nick_a}: 일주 {user_a_info.get('day_pillar')}"
        f" · 주요 오행 {user_a_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_a_info.get('gender') or '미상'}",
        f"- {nick_b}: 일주 {user_b_info.get('day_pillar')}"
        f" · 주요 오행 {user_b_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_b_info.get('gender') or '미상'}",
        "",
        f"본문에서 두 사람을 부를 때는 '{nick_a}님', '{nick_b}님' 으로만 호칭하십시오.",
        "",
        "[검색된 원전 구절]",
    ]
    for i, p in enumerate(passages, start=1):
        content = p.content if len(p.content) <= 600 else p.content[:600] + "…"
        lines.append(f"{i}. {p.citation}")
        lines.append(f"   {content}")
    lines.append("")
    lines.append("위 원전 구절을 근거로 JSON 을 반환하십시오.")
    return "\n".join(lines)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_pair_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort JSON extraction — tolerates code fences and trailing text."""
    if not text:
        return None
    text = text.strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last resort — find first '{' and matching outermost '}'
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


# --- Detailed self-saju interpretation (multi-section) -------------

_DETAILED_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자와 원전 문헌의 관련 구절을 바탕으로 "
    "한국어 심층 해석을 작성하는 도우미입니다.\n"
    "\n"
    "반드시 지킬 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- '검색된 원전 구절' 내용만 근거로 사용하고, 원전에 없는 내용은 추측하지 마십시오.\n"
    "- 사용자의 일주는 '[사주 결과]'에 적힌 값 그대로 지칭해야 합니다.\n"
    "- 건강·수명·재물·배우자에 대한 확정적 예언은 금지합니다.\n"
    "- '~할 것이다' 대신 '~경향이 있습니다', '~로 해석됩니다' 같은 완곡한 표현을 사용하십시오.\n"
    "- 각 카테고리 본문은 2~3 문장 (60~150자) 의 한국어로 작성하십시오.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "personality": "성격에 대한 2~3문장 해석.",\n'
    '  "love": "대인관계·연애운에 대한 2~3문장 해석.",\n'
    '  "wealth": "재물운에 대한 2~3문장 해석.",\n'
    '  "advice": "사용자에게 도움이 되는 행동/방향 추천 2~3문장."\n'
    "}\n"
    "\n"
    "건강·질병·수명에 대한 언급은 절대 포함하지 마십시오.\n"
    "\n"
    "원전 구절이 사주와 명확한 연결점이 없는 카테고리는 빈 문자열을 반환해도 됩니다."
)


def generate_detailed_interpretation(
    saju: SajuResponse,
    passages: list[RetrievedPassage],
    *,
    model: str = _MODEL,
) -> Optional[dict[str, str]]:
    """Generate a 5-section structured interpretation.

    Sections: personality / love / wealth / health / advice.
    Returns dict with those 5 keys (some values may be empty strings) or
    None on failure / no passages.
    """
    if not passages:
        return None
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_DETAILED_SYSTEM_PROMPT,
            input=_build_user_message(saju, passages),
            max_output_tokens=1200,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)  # reuse the same JSON extractor
        if parsed is None:
            return None
        return {
            "personality": str(parsed.get("personality") or ""),
            "love": str(parsed.get("love") or ""),
            "wealth": str(parsed.get("wealth") or ""),
            "advice": str(parsed.get("advice") or ""),
        }
    except Exception:
        return None


# --- 운명의 실타래 (커플 사주 심층 비교) ------------------------------

_DESTINY_SYSTEM_PROMPT = (
    "당신은 두 사용자의 사주 정보를 직접 비교해 깊이 있는 궁합 분석을 "
    "한국어로 작성하는 도우미입니다.\n"
    "\n"
    "반드시 지킬 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 두 사람을 가리킬 때는 [궁합 분석 입력]에 적힌 닉네임을 그대로 사용하고 "
    "뒤에 '님' 을 붙여 호칭하십시오. 예: '민수님', '예린님'. "
    "절대 'A님', 'B님', '사용자 A', '상대' 등으로 부르지 마십시오.\n"
    "- 두 사람의 일주(日柱)·주요 오행을 직접 인용하면서 비교하십시오. "
    "예: '민수님은 갑목(甲木) 일간으로 ..., 예린님은 신금(辛金) 일간으로 ...'.\n"
    "- 사주 용어를 쓰면 즉시 풀이를 함께 적으십시오.\n"
    "- 건강·수명·파탄 등 단정적 예언은 금지. '~경향이 있어요', '~잘 맞을 "
    "것 같아요' 같은 부드러운 표현 사용.\n"
    "- 각 섹션은 한국어 2~3 문장 (80~160자) 으로 작성.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "intro":       "두 분 사주의 첫인상과 전체 인상 요약",\n'
    '  "personality": "두 분 일주를 직접 비교한 성격 궁합",\n'
    '  "love_style":  "연애 스타일·표현 방식 비교",\n'
    '  "caution":     "갈등 가능성과 극복 방향",\n'
    '  "longterm":    "장기 전망과 관계 발전 방향"\n'
    "}\n"
)


def _build_destiny_message(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
) -> str:
    nick_a = user_a_info.get("nickname") or "사용자A"
    nick_b = user_b_info.get("nickname") or "사용자B"
    return "\n".join([
        "[궁합 분석 입력]",
        f"- 궁합 점수: {score} / 100",
        f"- {nick_a}: 일주 {user_a_info.get('day_pillar')}"
        f" · 천간 오행 {user_a_info.get('day_stem_element') or '미상'}"
        f" · 주요 오행 {user_a_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_a_info.get('gender') or '미상'}"
        f" · MBTI {user_a_info.get('mbti') or '미상'}",
        f"- {nick_b}: 일주 {user_b_info.get('day_pillar')}"
        f" · 천간 오행 {user_b_info.get('day_stem_element') or '미상'}"
        f" · 주요 오행 {user_b_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_b_info.get('gender') or '미상'}"
        f" · MBTI {user_b_info.get('mbti') or '미상'}",
        "",
        f"두 분({nick_a}, {nick_b})의 사주를 직접 비교해 5개 섹션의 깊이 있는 "
        f"궁합 풀이를 JSON 으로 작성하십시오. 본문에서는 반드시 두 사람을 "
        f"'{nick_a}님', '{nick_b}님' 으로만 호칭하십시오.",
    ])


def generate_destiny_analysis(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
    model: str = _MODEL,
) -> Optional[dict[str, str]]:
    """두 사람의 사주를 직접 비교한 5-섹션 심층 풀이 (운명의 실타래)."""
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_DESTINY_SYSTEM_PROMPT,
            input=_build_destiny_message(
                score=score, user_a_info=user_a_info, user_b_info=user_b_info,
            ),
            max_output_tokens=1500,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)
        if parsed is None:
            return None
        return {
            "intro": str(parsed.get("intro") or "").strip(),
            "personality": str(parsed.get("personality") or "").strip(),
            "love_style": str(parsed.get("love_style") or "").strip(),
            "caution": str(parsed.get("caution") or "").strip(),
            "longterm": str(parsed.get("longterm") or "").strip(),
        }
    except Exception:
        return None


# --- 데이트 장소 추천 ------------------------------------------------

_DATE_RECOMMENDATION_SYSTEM_PROMPT = (
    "당신은 두 사용자의 사주 궁합을 바탕으로 어울리는 데이트 장소를 "
    "한국어로 제안하는 도우미입니다.\n"
    "\n"
    "반드시 지킬 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- '~할 것이다' 대신 '~경향이 있어요', '~잘 맞을 것 같아요' 같은 부드러운 "
    "추천 표현을 사용하십시오.\n"
    "- 사주 전문 용어를 쓰면 즉시 풀이를 같이 적으십시오.\n"
    "- 데이트 장소는 한국 도시(서울/부산 등) 어디서나 갈 만한 일반 카테고리로 "
    "제안 (특정 가게 이름 X). 예: '한적한 산책로', '조용한 북카페', "
    "'활기찬 시장', '미술 전시회', '야경이 보이는 루프탑'.\n"
    "- 4~5개 장소 제안. 각 장소는 title (12자 이내) + description (40~80자) "
    "으로 작성.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "overview": "두 분의 데이트 스타일을 한 문단(60~120자)으로 요약",\n'
    '  "spots": [\n'
    '    {"title": "조용한 북카페",  "description": "이런 이유로 잘 맞아요..."},\n'
    '    {"title": "한적한 산책로",  "description": "..."},\n'
    '    {"title": "활기찬 시장",    "description": "..."},\n'
    '    {"title": "야경 루프탑",    "description": "..."}\n'
    "  ]\n"
    "}\n"
)


def _build_date_message(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
) -> str:
    nick_a = user_a_info.get("nickname") or "사용자A"
    nick_b = user_b_info.get("nickname") or "사용자B"
    return "\n".join([
        "[궁합 분석 입력]",
        f"- 궁합 점수: {score} / 100",
        f"- {nick_a}: 일주 {user_a_info.get('day_pillar')}"
        f" · 주요 오행 {user_a_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_a_info.get('gender') or '미상'}"
        f" · MBTI {user_a_info.get('mbti') or '미상'}",
        f"- {nick_b}: 일주 {user_b_info.get('day_pillar')}"
        f" · 주요 오행 {user_b_info.get('dominant_element') or '미상'}"
        f" · 성별 {user_b_info.get('gender') or '미상'}"
        f" · MBTI {user_b_info.get('mbti') or '미상'}",
        "",
        f"위 정보를 바탕으로 두 분({nick_a}, {nick_b})에게 잘 맞는 데이트 "
        "장소 4~5곳을 JSON 으로 추천하십시오. description 본문에서 두 사람을 "
        f"부를 때는 '{nick_a}님', '{nick_b}님' 으로만 호칭하십시오.",
    ])


def generate_date_recommendation(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
    model: str = _MODEL,
) -> Optional[dict[str, Any]]:
    """두 사람의 사주 정보로 데이트 장소 추천 (overview + 4~5개 spots)."""
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_DATE_RECOMMENDATION_SYSTEM_PROMPT,
            input=_build_date_message(
                score=score, user_a_info=user_a_info, user_b_info=user_b_info,
            ),
            max_output_tokens=1200,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)
        if parsed is None:
            return None
        raw_spots = parsed.get("spots") or []
        spots: list[dict[str, str]] = []
        for s in raw_spots:
            if not isinstance(s, dict):
                continue
            t = str(s.get("title") or "").strip()
            d = str(s.get("description") or "").strip()
            if t and d:
                spots.append({"title": t, "description": d})
        return {
            "overview": str(parsed.get("overview") or "").strip(),
            "spots": spots,
        }
    except Exception:
        return None


# --- 자미두수 interpretation -----------------------------------------

_JAMIDUSU_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자 정보를 바탕으로 "
    "자미두수(紫微斗數) 12궁과 14주성 관점에서 한국어 풀이를 작성하는 도우미입니다.\n"
    "\n"
    "반드시 지킬 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 12궁 이름의 한자(命宮·財帛宮 등)는 그대로 두되, 본문(description)은 한자 없이 "
    "쉬운 한국어로만 작성하십시오. 예: '명궁은 본인의 기질을 보여주는 자리예요.'\n"
    "- 사용자의 일주·오행·생년월일은 [사주 결과]에 적힌 그대로 활용하십시오.\n"
    "- 건강·수명·질병·파탄·불륜에 대한 확정적 예언은 금지합니다.\n"
    "- '~할 것이다' 대신 '~경향이 있습니다', '~로 해석됩니다' 같은 완곡한 표현을 사용하십시오.\n"
    "- 12궁 description 은 각각 한 문장 (30~80자) 으로 간결히 작성하십시오.\n"
    "- main_stars_summary 와 overview 는 각각 2~3 문장 (80~150자) 으로 작성하십시오.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "overview": "사용자의 자미두수 전반에 대한 2~3문장 요약",\n'
    '  "palaces": [\n'
    '    {"name": "命宮 (명궁)",   "description": "..."},\n'
    '    {"name": "兄弟宮 (형제궁)", "description": "..."},\n'
    '    {"name": "夫妻宮 (부처궁)", "description": "..."},\n'
    '    {"name": "子女宮 (자녀궁)", "description": "..."},\n'
    '    {"name": "財帛宮 (재백궁)", "description": "..."},\n'
    '    {"name": "疾厄宮 (질액궁)", "description": "..."},\n'
    '    {"name": "遷移宮 (천이궁)", "description": "..."},\n'
    '    {"name": "交友宮 (교우궁)", "description": "..."},\n'
    '    {"name": "官祿宮 (관록궁)", "description": "..."},\n'
    '    {"name": "田宅宮 (전택궁)", "description": "..."},\n'
    '    {"name": "福德宮 (복덕궁)", "description": "..."},\n'
    '    {"name": "父母宮 (부모궁)", "description": "..."}\n'
    '  ],\n'
    '  "main_stars_summary": "14주성 중 명궁·재백궁·관록궁에 위치하는 주성들이 사용자의 어떤 측면을 부각하는지 2~3문장 요약."\n'
    "}\n"
    "\n"
    "12궁 모두 빠짐없이 채우되, 같은 묘사를 반복하지 마십시오."
)


def _build_jamidusu_message(saju: SajuResponse) -> str:
    day_pillar = saju.pillars[2]
    ep = saju.element_profile
    named = [
        ("목", ep.wood), ("화", ep.fire), ("토", ep.earth),
        ("금", ep.metal), ("수", ep.water),
    ]
    dom_name, dom_count = max(named, key=lambda x: x[1])

    inp = saju.input_summary
    parts = [
        "[사주 결과]",
        f"- 생년월일: {inp.birth_date}"
        + (f" {inp.birth_time}" if inp.birth_time else " (시간 모름)"),
        f"- 양/음력: {inp.calendar_type}"
        + (" (윤달)" if inp.is_leap_month else ""),
        f"- 성별: {inp.gender or '미상'}",
        f"- 일주: {day_pillar.combined} (천간 {day_pillar.stem} · 지지 {day_pillar.branch})",
        f"- 오행 분포: 목 {ep.wood} · 화 {ep.fire} · 토 {ep.earth} · 금 {ep.metal} · 수 {ep.water}",
    ]
    if dom_count > 0:
        parts.append(f"- 주요 오행: {dom_name}")
    parts.append("")
    parts.append(
        "위 사주 정보를 토대로 자미두수 12궁과 14주성 관점의 풀이 JSON 을 반환하십시오."
    )
    return "\n".join(parts)


def generate_jamidusu_interpretation(
    saju: SajuResponse,
    *,
    model: str = _MODEL,
) -> Optional[dict[str, Any]]:
    """Generate 자미두수 12궁·14주성 풀이 grounded on the user's saju.

    Returns a dict with keys: overview, palaces (list[{name,description}]),
    main_stars_summary. Returns None on failure.
    """
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_JAMIDUSU_SYSTEM_PROMPT,
            input=_build_jamidusu_message(saju),
            max_output_tokens=2000,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)
        if parsed is None:
            return None
        raw_palaces = parsed.get("palaces") or []
        palaces: list[dict[str, str]] = []
        for p in raw_palaces:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "").strip()
            desc = str(p.get("description") or "").strip()
            if name and desc:
                palaces.append({"name": name, "description": desc})
        return {
            "overview": str(parsed.get("overview") or "").strip(),
            "palaces": palaces,
            "main_stars_summary": str(parsed.get("main_stars_summary") or "").strip(),
        }
    except Exception:
        return None


def generate_pair_recommendation(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
    passages: list[RetrievedPassage],
    model: str = _MODEL,
) -> Optional[dict[str, Any]]:
    """Generate structured pair recommendation. Returns dict or None on failure.

    Dict keys: strengths, cautions, conversation_starters, summary.
    """
    if not passages:
        return None
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_PAIR_SYSTEM_PROMPT,
            input=_build_pair_message(
                score=score,
                user_a_info=user_a_info,
                user_b_info=user_b_info,
                passages=passages,
            ),
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)
        if parsed is None:
            return None
        # Normalize — ensure list fields are lists, summary is str.
        return {
            "strengths": list(parsed.get("strengths") or []),
            "cautions": list(parsed.get("cautions") or []),
            "conversation_starters": list(parsed.get("conversation_starters") or []),
            "summary": parsed.get("summary") or None,
        }
    except Exception:
        return None
