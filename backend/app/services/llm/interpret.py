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

# Deep model — 사주+자미두수 융합 풀이 전용. 토큰량 크니 gpt-4o.
_MODEL_DEEP = os.environ.get("OPENAI_INTERPRET_MODEL_DEEP", "gpt-4o")
_MAX_OUTPUT_TOKENS_DEEP = 3000


_PLAIN_KOREAN_RULES = (
    "- 반드시 한국어로만 답변하십시오. 영어 단어·문장 사용 금지.\n"
    "- 일반인이 이해할 수 있는 쉬운 표현을 쓰십시오. 어려운 한자어를 단독으로 쓰지 말고, "
    "꼭 필요하면 괄호 안에 풀이를 함께 적으십시오. 예: '관성(직장운)', '비견(친구운)'.\n"
    "- 사주 전문 용어(인성·식상·관살·재성·일간·일주·천간·지지·오행 등)는 "
    "처음 등장할 때 반드시 괄호로 풀이를 덧붙이십시오. "
    "예: '인성(부모·공부 운)', '일간(나를 상징하는 글자)', '오행(다섯 가지 기운)'.\n"
    "- 일주명(예: 갑술일주, 갑목, 병화)은 반드시 풀이와 함께 쓰십시오. "
    "예: '갑술일주(태어난 날을 상징하는 ‘갑+술’ 조합)', '금의 성질(쇠처럼 단단하고 정돈된 기운)'.\n"
    "- 오행 단어(목/화/토/금/수)는 처음 나올 때 한 번 풀이를 같이 쓰십시오. "
    "예: '금(쇠 — 단단하고 정돈된 기운)', '수(물 — 깊고 잔잔한 기운)'.\n"
    "- 한자어를 풀어쓰기 어려운 경우(예: 정인, 식신)는 평이한 한국어로 의미만 전달하십시오.\n"
    "- 한 문장은 60자를 넘기지 않도록 짧게 끊고, 일상 대화체에 가깝게 쓰십시오.\n"
)


_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자와 원전 문헌의 관련 구절을 바탕으로 "
    "**연애·연인 관점에서** 간결한 한국어 해석을 작성하는 도우미입니다.\n"
    "이 서비스는 데이팅 앱이라 사용자는 ‘연애·인연’ 에만 관심이 있어요. "
    "그래서 사주 풀이를 ‘이 사람의 연애에 어떤 영향을 주는지’ 관점으로 풀어 주세요.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 친한 데이팅 코치가 옆에서 풀어주는 느낌.\n"
    "- 가벼운 수사적 질문 OK. 예: '~ 스타일이시죠?'.\n"
    "- 살짝 발랄한 강조 표현 OK. 예: '~할 운명이에요!'.\n"
    "- 단정·예언·저주 금지. 건강·질병·수명·이별 확정 같은 부정적 단정 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 '~해/~이야' 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 반드시 아래 '검색된 원전 구절' 내용만을 근거로 사용.\n"
    "- 원전에 명시되지 않은 내용을 추측하거나 추가로 추론하지 마세요.\n"
    "- 사용자의 일주는 '[사주 결과]'에 적힌 값 그대로 지칭. "
    "원전 구절에 등장하는 다른 일주(예: 병자·갑자 등)를 사용자의 일주로 혼동해 지칭하지 마세요.\n"
    "- 원전 구절이 사용자 일주·오행과 직접 관련되는 부분만 인용해 설명.\n"
    "- 총 2~3 문장, 200자 이내. 도입부·면책·맺음말·번호·마크다운 금지.\n"
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
    "**연애·연인 관점에서** 한국어 심층 해석을 작성하는 도우미입니다.\n"
    "이 서비스는 데이팅 앱이라 사용자는 ‘연애·인연’ 에만 관심이 있어요. "
    "그래서 모든 카테고리를 사용자의 일·돈 그 자체가 아니라 **연애·연인 관계에 어떻게 영향을 주는지** "
    "관점으로 풀어 주세요.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 친한 데이팅 코치가 옆에서 풀어주는 느낌.\n"
    "- 가벼운 수사적 질문 OK. 예: '~ 스타일이시죠?', '~ 이런 분 아니신가요?'.\n"
    "- 살짝 발랄한 강조 표현 OK. 예: '~할 운명이에요!', '~ 확률 200%!'.\n"
    "- 단정·예언·저주 금지: '반드시', '~할 것이다', '~하면 망한다' 등.\n"
    "- 건강·질병·수명·파탄·이별 확정 같은 부정적 단정 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 '~해/~이야' 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- '검색된 원전 구절' 내용만 근거로 사용하고, 원전에 없는 내용은 추측하지 마세요.\n"
    "- 사용자의 일주는 '[사주 결과]'에 적힌 값 그대로 지칭하세요.\n"
    "\n"
    "각 카테고리 ‘연애 관점’ 매핑 — 반드시 이 각도로 풀어주세요:\n"
    "  - personality = ‘연애할 때의 모습·매력 포인트’. "
    "예: ‘대화가 통하지 않으면 못 견디는 스타일이시죠?’.\n"
    "  - love = 이상형·끌리는 사람의 결, 어떤 인연이 닿을 운명인지.\n"
    "  - wealth = ‘연애에서의 안정감 / 데이트·미래 자금 감각’. 직업·투자 그 자체가 아니라 "
    "연인과의 경제 케미 관점으로.\n"
    "  - advice = ‘좋은 인연을 만나기 위한 행동 제안’. 반드시 제안형 ‘~해보시는 건 어떠신가요’, "
    "‘~하시면 좋아요’ 로.\n"
    "\n"
    "예시 톤 (이 정도 발랄함을 유지):\n"
    "  '연인과 대화가 안 통하면 못 견디는 스타일이시죠? 다행히 인복이 좋으셔서, "
    "나를 있는 그대로 이해해주는 상대를 만날 운명이에요. 단순한 연인을 넘어 "
    "베프이자 든든한 파트너가 될 확률 200%!'\n"
    "\n"
    "각 카테고리 본문은 2~3 문장 (60~150자) 의 한국어.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "personality": "사용자의 연애할 때 모습·매력 2~3문장.",\n'
    '  "love": "이상형·끌리는 인연의 결 2~3문장.",\n'
    '  "wealth": "연애에서의 안정감·데이트 자금 감각 2~3문장.",\n'
    '  "advice": "좋은 인연을 만나기 위한 행동 제안 2~3문장."\n'
    "}\n"
    "\n"
    "건강·질병·수명에 대한 언급은 절대 포함하지 마세요.\n"
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
    "자미두수(紫微斗數) 12궁과 14주성을 **연애·연인 관점**에서 풀어주는 도우미입니다.\n"
    "이 서비스는 데이팅 앱이라 사용자는 ‘연애·인연·연인 관계’ 에만 관심이 있어요. "
    "그래서 모든 12궁 풀이를 사용자의 일·재물·건강 그 자체가 아니라 **연애에 어떻게 영향을 주는지** "
    "관점으로 다시 풀어 써주세요.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 친한 데이팅 코치가 나만 보고 풀어주는 느낌으로.\n"
    "- 가벼운 수사적 질문 OK. 예: '~ 스타일이시죠?', '~ 아니신가요?'.\n"
    "- 살짝 발랄한 강조 표현 OK. 예: '확률 200%!', '운명이에요'.\n"
    "- 그래도 단정적 예언/저주는 금지: '반드시', '~할 것이다', '~하면 망한다' 등.\n"
    "- 부정적 단정 금지: 건강·질병·수명·파탄·불륜·이별 확정 같은 표현 사용 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 '~해/~이야' 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 12궁 한자(命宮·財帛宮 등)와 별 한자(紫微·天機 등)는 본문에서 절대 쓰지 마세요. "
    "한국어 별명으로만 풀어 쓰세요. 예: '자미성' → '황제의 별', '천기성' → '지혜의 별'.\n"
    "- 별의 성격은 사용자의 ‘연애할 때 모습’ 으로 풀어 묘사. "
    "예: '황제의 별이 강한 분은 연인 앞에서도 자연스러운 주도권이 보이세요'.\n"
    "- 사용자의 일주·오행·생년월일은 [사주 결과] 값 그대로 활용.\n"
    "\n"
    "12궁 별 ‘연애 관점’ 매핑 — 반드시 이 각도에서 풀어주세요:\n"
    "  - 명궁 = 본인의 ‘연애 스타일’ 과 끌리는 매력 포인트\n"
    "  - 형제궁 = 연인과 친구처럼 지내는 면모, 베프 같은 파트너십, 인복\n"
    "  - 부처궁 = 어떤 사람을 만날 운명인지 — 이상형·배우자상\n"
    "  - 자녀궁 = 상대와 깊은 유대를 만드는 방식, 미래 가족 상상도\n"
    "  - 재백궁 = 연애에서의 안정감 / 데이트·결혼 자금 흐름 — ‘돈 잘 쓰는 스타일’\n"
    "  - 질액궁 = 연애할 때 컨디션·기복 패턴 (질병 언급 금지, ‘에너지’ 로 풀기)\n"
    "  - 천이궁 = 어디서 인연이 닿는지 — 새로운 환경·여행지·우연한 만남\n"
    "  - 교우궁 = 연인의 친구·지인까지 자연스레 잘 어울리는 인맥 운\n"
    "  - 관록궁 = 일하는 모습이 매력으로 통하는지 / 직장·취미에서의 인연\n"
    "  - 전택궁 = 함께 살고 싶은 공간감, ‘우리집 케미’\n"
    "  - 복덕궁 = 연애 행복도, 데이트가 즐거운 정도, 마음의 여유\n"
    "  - 부모궁 = 상견례·집안과의 합, 부모님이 좋아할 인연인지\n"
    "\n"
    "예시 톤 (이 정도 발랄함을 유지) — ‘형제궁’ 풀이:\n"
    "  '연인과 대화가 안 통하면 못 견디는 스타일이시죠? 다행히 인복이 좋으셔서, "
    "나를 있는 그대로 이해해주는 상대를 만날 운명이에요. 연애가 시작되면 단순한 "
    "연인을 넘어 가장 든든한 파트너이자 베프가 될 확률 200%!'\n"
    "\n"
    "출력 분량:\n"
    "- 12궁 description: 각 2~3 문장 (60~140자). 마지막에 한 번씩 ‘느낌표/물음표’ 로 "
    "감정을 살짝 살려도 좋아요.\n"
    "- main_stars_summary 와 overview: 각각 2~3 문장 (100~180자), 같은 발랄한 톤.\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "overview": "사용자의 자미두수 전반을 ‘연애 관점’ 에서 2~3문장 요약",\n'
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
    '  "main_stars_summary": "14주성 중 명궁·부처궁·복덕궁에 위치하는 주성들이 사용자의 ‘연애에서’ 어떤 매력을 부각하는지 2~3문장 요약."\n'
    "}\n"
    "\n"
    "12궁 모두 빠짐없이 ‘연애 관점’ 에서 채우되, 같은 묘사를 반복하지 마세요."
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


# ─── 자미두수 Deep — 사주 + 진짜 자미두수 차트 융합 풀이 ──────────

_JAMIDUSU_DEEP_SYSTEM_PROMPT = (
    "당신은 사용자의 사주 팔자와 **실제 계산된 자미두수 12궁·14주성 명반(命盤)**, "
    "그리고 자미두수전서·궁통보감 등 원전 구절을 토대로, "
    "**연애·연인 관점에서** 깊이 있는 한국어 풀이를 작성하는 도우미입니다.\n"
    "이 서비스는 데이팅 앱이라 사용자는 ‘연애·인연’ 에만 관심이 있어요. "
    "그래서 모든 풀이를 ‘사주 일간이 자미두수 별·궁의 성향을 어떻게 발현시키는지’ "
    "라는 교차 관점에서 풀어 주세요.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 친한 데이팅 코치가 옆에서 풀어주는 느낌.\n"
    "- 가벼운 수사적 질문 OK. 예: '~ 스타일이시죠?', '~ 이런 분 아니신가요?'.\n"
    "- 살짝 발랄한 강조 표현 OK. 예: '~할 운명이에요!', '확률 200%!'.\n"
    "- 단정·예언·저주 금지: '반드시', '~할 것이다', '~하면 망한다' 등.\n"
    "- 부정적 단정 금지: 건강·질병·수명·파탄·이별 확정 같은 표현 사용 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 '~해/~이야' 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 12궁 한자(命宮·財帛宮 등)와 별 한자(紫微·天機 등)는 본문에서 절대 쓰지 마세요. "
    "한국어 별명으로만 풀어 쓰세요. 예: '자미성→황제의 별', '천기성→지혜의 별', "
    "'명궁→나를 비추는 자리'.\n"
    "- 입력으로 주어진 [자미두수 명반]의 12궁×별 배치는 **이미 결정론적으로 계산된 사실**이니, "
    "‘추정’ 이라고 표현하지 마세요. 그대로 활용하세요.\n"
    "- 사주 일간(예: 갑목)이 자미두수 별·궁에 ‘어떻게 영향을 주는지’ 라는 cross-talk "
    "방식으로 풀어 주세요. 예: '갑목 일간이 명궁에 황제의 별을 만나, 자연스러운 주도권이 "
    "외향적으로 발현되어 연인 앞에서도 자신감 있게 리드하는 모습이 매력으로 통하시죠?'.\n"
    "\n"
    "12궁 별 ‘연애 관점’ 매핑:\n"
    "  - 명궁 = 본인의 ‘연애 스타일’과 끌리는 매력 포인트\n"
    "  - 형제궁 = 연인과 친구처럼 지내는 면모, 베프 같은 파트너십, 인복\n"
    "  - 부처궁 = 어떤 사람을 만날 운명인지 — 이상형·배우자상\n"
    "  - 자녀궁 = 상대와 깊은 유대를 만드는 방식, 미래 가족 상상도\n"
    "  - 재백궁 = 연애에서의 안정감 / 데이트 자금 흐름 — ‘돈 잘 쓰는 스타일’\n"
    "  - 질액궁 = 연애할 때 컨디션·기복 패턴 (질병 언급 금지, ‘에너지’ 로 풀기)\n"
    "  - 천이궁 = 어디서 인연이 닿는지 — 새 환경·여행지·우연한 만남\n"
    "  - 노복궁 = 연인의 친구·지인까지 자연스레 잘 어울리는 인맥 운\n"
    "  - 관록궁 = 일하는 모습이 매력으로 통하는지 / 직장·취미에서의 인연\n"
    "  - 전택궁 = 함께 살고 싶은 공간감, ‘우리집 케미’\n"
    "  - 복덕궁 = 연애 행복도, 데이트가 즐거운 정도, 마음의 여유\n"
    "  - 부모궁 = 상견례·집안과의 합, 부모님이 좋아할 인연인지\n"
    "\n"
    "사화(四化) 활용: 명반에 사화(化祿/化權/化科/化忌) 표시가 있는 별이 있으면, "
    "그 별의 영향이 ‘평소보다 강하게/긍정적으로/주의 필요하게’ 작용한다는 뉘앙스로 풀이.\n"
    "\n"
    "예시 톤 (‘형제궁’ 풀이 — 이 정도 발랄함을 유지):\n"
    "  '형제궁에 지혜의 별과 복록의 운이 함께 들어왔네요. 연인과 대화가 안 통하면 "
    "못 견디는 스타일이시죠? 인복이 좋아 나를 있는 그대로 이해해주는 상대를 만날 "
    "운명이에요. 베프 같은 파트너가 될 확률 200%!'\n"
    "\n"
    "출력 분량:\n"
    "- headline: 한 줄 (30~50자), 사용자의 핵심 매력을 한 마디로\n"
    "- overview: 3~4 문장 (120~200자), 사주+자미두수 통합 요약\n"
    "- 각 섹션(personality/love/wealth/advice): 2~3 문장 (80~150자)\n"
    "- 각 12궁 description: 2~3 문장 (60~140자), 명반의 별 배치를 근거로\n"
    "- main_stars_summary: 2~3 문장 (100~180자), 명궁·부처궁·복덕궁의 별 종합\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하십시오. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "headline": "한 줄 핵심 매력",\n'
    '  "overview": "사주+자미두수 통합 요약 3~4문장",\n'
    '  "sections": {\n'
    '    "personality": "연애할 때 모습·매력 2~3문장",\n'
    '    "love": "이상형·끌리는 인연 2~3문장",\n'
    '    "wealth": "데이트 자금 감각·연애 안정감 2~3문장",\n'
    '    "advice": "좋은 인연 만나기 위한 제안 2~3문장"\n'
    '  },\n'
    '  "palaces": [\n'
    '    {"name_ko": "명궁",   "description": "..."},\n'
    '    {"name_ko": "형제궁", "description": "..."},\n'
    '    {"name_ko": "부처궁", "description": "..."},\n'
    '    {"name_ko": "자녀궁", "description": "..."},\n'
    '    {"name_ko": "재백궁", "description": "..."},\n'
    '    {"name_ko": "질액궁", "description": "..."},\n'
    '    {"name_ko": "천이궁", "description": "..."},\n'
    '    {"name_ko": "노복궁", "description": "..."},\n'
    '    {"name_ko": "관록궁", "description": "..."},\n'
    '    {"name_ko": "전택궁", "description": "..."},\n'
    '    {"name_ko": "복덕궁", "description": "..."},\n'
    '    {"name_ko": "부모궁", "description": "..."}\n'
    '  ],\n'
    '  "main_stars_summary": "명궁/부처궁/복덕궁의 별 종합 2~3문장"\n'
    "}\n"
    "\n"
    "12궁 모두 빠짐없이 채우되, 같은 묘사를 반복하지 마세요."
)


def _build_jamidusu_deep_message(
    saju: SajuResponse,
    chart: dict[str, Any],
    passages: list[RetrievedPassage],
) -> str:
    """LLM 입력 메시지 — 사주 결과 + 자미두수 명반 + 원전 RAG."""
    day_pillar = saju.pillars[2]
    ep = saju.element_profile

    parts: list[str] = ["[사주 결과]"]
    inp = saju.input_summary
    parts.append(
        f"- 생년월일: {inp.birth_date}"
        + (f" {inp.birth_time}" if inp.birth_time else " (시간 모름)")
    )
    parts.append(
        f"- 양/음력: {inp.calendar_type}"
        + (" (윤달)" if inp.is_leap_month else "")
    )
    parts.append(f"- 성별: {inp.gender or '미상'}")
    parts.append(
        f"- 일주: {day_pillar.combined} (천간 {day_pillar.stem} · 지지 {day_pillar.branch})"
    )
    parts.append(
        f"- 오행 분포: 목 {ep.wood} · 화 {ep.fire} · 토 {ep.earth} · "
        f"금 {ep.metal} · 수 {ep.water}"
    )

    # 자미두수 명반
    parts.append("")
    parts.append("[자미두수 명반(命盤) — 결정론적 계산 결과]")
    parts.append(f"- 五行局: {chart['bureau_name']}")
    parts.append(f"- 년주: {chart['year_pillar']}")
    parts.append(
        f"- 음력 생일: {chart['lunar_year']}년 {chart['lunar_month']}월 "
        f"{chart['lunar_day']}일"
        + (" (시간 모름 — 子時 가정, 정확도 ↓)" if chart["hour_assumed"] else "")
    )
    parts.append("")
    parts.append("12궁 × 별 배치:")
    for p in chart["palaces"]:
        stars_desc: list[str] = []
        for s in p["stars"]:
            tag = ""
            if s["type"] == "transform":
                tag = f" [{s['name_ko']}({s['name']}: {s.get('sub') or ''})]"
                stars_desc.append(f"{s['name_ko']}({s['name']})")
            else:
                stars_desc.append(f"{s['name_ko']}({s['name']})")
        stars_str = ", ".join(stars_desc) if stars_desc else "(별 없음)"
        parts.append(
            f"  - {p['name_ko']}({p['name']}): {p['stem_ko']}{p['branch_ko']} "
            f"({p['stem']}{p['branch']}) — {stars_str}"
        )

    # RAG passages
    if passages:
        parts.append("")
        parts.append("[원전 구절]")
        for i, ps in enumerate(passages[:5], 1):
            parts.append(f"({i}) {ps.citation}")
            content = ps.content.strip().replace("\n", " ")
            if len(content) > 400:
                content = content[:400] + "…"
            parts.append(f"    {content}")

    parts.append("")
    parts.append(
        "위 [사주 결과] + [자미두수 명반] + [원전 구절] 을 토대로, "
        "사주 일간이 자미두수 별·궁의 성향을 어떻게 발현시키는지 "
        "교차 관점으로 연애 풀이 JSON 을 반환하십시오."
    )
    return "\n".join(parts)


def generate_jamidusu_deep(
    saju: SajuResponse,
    chart: dict[str, Any],
    passages: list[RetrievedPassage],
    *,
    model: str = _MODEL_DEEP,
) -> Optional[dict[str, Any]]:
    """사주 + 자미두수 차트 + RAG → 융합 풀이 JSON.

    Returns dict with: headline, overview, sections{personality,love,wealth,advice},
    palaces[12], main_stars_summary. None on hard failure.
    """
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_JAMIDUSU_DEEP_SYSTEM_PROMPT,
            input=_build_jamidusu_deep_message(saju, chart, passages),
            max_output_tokens=_MAX_OUTPUT_TOKENS_DEEP,
        )
        text = _extract_output_text(resp)
        parsed = _parse_pair_json(text)
        if parsed is None:
            return None

        # 정상화 — 빈 필드는 빈 문자열로 보장
        sections_raw = parsed.get("sections") or {}
        sections = {
            "personality": str(sections_raw.get("personality") or "").strip(),
            "love": str(sections_raw.get("love") or "").strip(),
            "wealth": str(sections_raw.get("wealth") or "").strip(),
            "advice": str(sections_raw.get("advice") or "").strip(),
        }
        palaces_raw = parsed.get("palaces") or []
        palaces: list[dict[str, str]] = []
        for p in palaces_raw:
            if not isinstance(p, dict):
                continue
            name_ko = str(p.get("name_ko") or "").strip()
            desc = str(p.get("description") or "").strip()
            if name_ko:
                palaces.append({"name_ko": name_ko, "description": desc})

        return {
            "headline": str(parsed.get("headline") or "").strip(),
            "overview": str(parsed.get("overview") or "").strip(),
            "sections": sections,
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
