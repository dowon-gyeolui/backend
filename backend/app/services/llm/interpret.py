from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from app.schemas.saju import SajuResponse

_MODEL = os.environ.get("OPENAI_INTERPRET_MODEL", "gpt-4o-mini")
_MAX_OUTPUT_TOKENS = 400

_MODEL_DEEP = os.environ.get("OPENAI_INTERPRET_MODEL_DEEP", "gpt-4.1")
_MAX_OUTPUT_TOKENS_DEEP = 4500


_PLAIN_KOREAN_RULES = (
    "- 반드시 한국어로만 답변하십시오. 영어 단어·문장 사용 금지.\n"
    "- ★가장 중요★ 독자는 사주를 전혀 모르는 일반인입니다. 사주 전문용어(한자말)를 "
    "그대로 쓰지 말고 먼저 '쉬운 일상어'로 풀어 쓰십시오. 한자 용어를 꼭 써야 하면 "
    "쉬운 설명을 괄호로 반드시 함께 적으십시오.\n"
    "- 아래 '쉬운 풀이 사전'을 그대로 활용해 자연스럽게 녹여 쓰십시오:\n"
    "  · 오행(다섯 기운): 목=나무(자라남·성장), 화=불(열정·밝음), 토=흙(안정·포용), "
    "금=쇠(단단함·원칙), 수=물(지혜·차분·유연)\n"
    "  · 일주=태어난 날이 상징하는 '나 자신', 일진=오늘 하루의 기운\n"
    "  · 십성(나와의 관계 유형): 정재·편재=이성·연애운, 정관·편관=진중한 인연·매력, "
    "식신·상관=표현력·매력 발산, 정인·편인=따뜻함·예민한 직관\n"
    "  · 도화=이성을 끌어당기는 매력, 천을귀인=귀인의 도움이 따르는 좋은 기운, "
    "삼합·육합=잘 어울리는 조합, 육충=살짝 부딪힐 수 있어 조심\n"
    "- 예: '물의 기운'이라고만 쓰지 말고 '물의 기운(생각이 깊고 차분한 결)'처럼, "
    "'편인'이라고만 쓰지 말고 '편인(독특한 매력과 예민한 직관)'처럼 풀이를 붙이십시오.\n"
    "- 한자(壬子·甲木 등)는 본문에 그대로 노출하지 말고 쉬운 우리말로만 풀어 쓰십시오.\n"
    "- 한 문장은 60자를 넘기지 않도록 짧게 끊고, 일상 대화체로 쓰십시오.\n"
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
    citation: str
    content: str

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
    "  - personality = ‘연애할 때 나타나는 성격’. 리드형 / 헌신형 / 자유분방형 / "
    "안정형 같은 유형으로 분류해 서술하세요. 성격적 결함이나 부정적인 단어는 "
    "직접 노출하지 말고 매력으로 환원해 표현하세요. "
    "예: ‘평소와 다르게 연애할 때는 상대방을 리드하며 이끄는 것을 좋아해요. "
    "진솔한 대화가 가능하면서도 친구같이 장난도 칠 수 있는 성격이에요.’\n"
    "  - love = ‘숨겨진 이상형 / 본능적으로 끌리는 사람’ 을 정의하고, 나이대·성향 등 "
    "구체적인 스타일을 제안하세요. "
    "예: ‘안정적이고 친구같이 편한 연애를 추구할 확률이 높아요. 나이대가 비슷하고 "
    "이야기가 잘 통하는 사람을 찾아보세요!’ 또는 ‘통통 튀는 매력의 연하와 잘 맞을 "
    "확률이 높아요. 다만 5살 이상 차이나면 맞춰가는 데 시간이 걸려요.’\n"
    "  - wealth = ‘데이트·결혼관에서의 경제적 스타일’. "
    "예: ‘화끈하게 쓰고 즐기는 데이트를 선호해요. 맛집·핫플 탐방을 좋아할 확률이 "
    "높아요!’ 또는 ‘가성비와 실속을 중시해요. 계획적인 소비로 안정적인 미래를 함께 "
    "그릴 수 있는 스마트한 연애를 추구해요.’\n"
    "  - advice = ‘매칭 확률을 높이는 실전 팁’. 프로필 사진 팁, 선톡·대화 팁, "
    "마음가짐 조언처럼 앱 안에서 바로 실천할 행동 지침을 제안형으로 알려주세요. "
    "오행 기운을 근거로 연결하면 좋아요. "
    "예: ‘물(水)의 기운이 많아 생각이 많아질 수 있어요. 마음에 드는 상대가 있다면 "
    "고민은 매칭만 늦출 뿐! 먼저 가벼운 인사말로 운을 띄워보세요.’\n"
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
    '  "personality": "연애할 때 나타나는 성격을 유형(리드형/헌신형 등)으로 2~3문장.",\n'
    '  "love": "숨겨진 이상형·본능적으로 끌리는 스타일 + 구체적 제안 2~3문장.",\n'
    '  "wealth": "데이트·결혼관에서의 경제적 스타일 2~3문장.",\n'
    '  "advice": "매칭 확률을 높이는 앱 내 실전 팁(사진·선톡·마음가짐) 2~3문장."\n'
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


_JAMIDUSU_DEEP_SYSTEM_PROMPT = (
    "너는 소개팅 앱의 자미두수 기반 연애 분석가다.\n"
    "입력된 사용자의 자미두수 명반 정보를 바탕으로, 12궁을 연애 관점에서 쉽고 명확하게 해석해라.\n"
    "\n"
    "해석 기준:\n"
    "- 정통 자미두수 용어를 그대로 길게 설명하지 말고, 소개팅 앱 사용자가 이해할 수 있는 말로 바꿔라.\n"
    "- 각 궁은 연애, 소개팅, 관계 패턴과 연결해서 풀이해라.\n"
    "- 단정적인 표현은 피하고 ‘그럴 가능성이 있어요’, ‘이런 경향이 보여요’, "
    "’이런 관계에서 편안함을 느끼기 쉬워요’처럼 부드럽게 말해라.\n"
    "- 무섭거나 부정적인 예언, 이별 단정, 결혼 실패, 건강 문제, 재물 손실 같은 표현은 사용하지 마라.\n"
    "- 각 궁의 별 이름은 근거로만 짧게 활용하고, 핵심은 사용자가 바로 이해할 수 있는 연애 해석으로 작성해라.\n"
    "- 모든 궁의 문장 길이와 톤을 비슷하게 맞춰라.\n"
    "- 좋은 점만 말하지 말고, 각 궁마다 작은 주의점이나 연애 팁을 love_tip 에 1개씩 넣어라.\n"
    "- 반드시 한국어로만 작성해라. 다정한 존댓말을 사용해라.\n"
    "\n"
    "12궁 app_title (반드시 이 값을 그대로 사용):\n"
    "명궁=나의 기본 매력, 형제궁=편한 관계 케미, 부처궁=내가 끌리는 사람, "
    "자녀궁=관계의 미래감, 재백궁=데이트 돈 성향, 질액궁=연애 스트레스, "
    "천이궁=인연이 생기는 곳, 노복궁=주변 사람과의 케미, 관록궁=일과 연애 균형, "
    "전택궁=편안한 관계 방식, 복덕궁=연애 만족감, 부모궁=진지한 관계의 분위기\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력하라. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    ‘  "palaces": [\n’
    ‘    {\n’
    ‘      "name_ko": "명궁",\n’
    ‘      "app_title": "나의 기본 매력",\n’
    ‘      "summary": "한 줄 요약 1문장",\n’
    ‘      "love_interpretation": "연애 해석 2~3문장",\n’
    ‘      "love_tip": "연애 팁 1문장",\n’
    ‘      "keywords": ["키워드1", "키워드2", "키워드3"]\n’
    ‘    }\n’
    ‘  ]\n’
    "}\n"
    "\n"
    "명궁, 형제궁, 부처궁, 자녀궁, 재백궁, 질액궁, 천이궁, 노복궁, 관록궁, 전택궁, 복덕궁, 부모궁 "
    "순서로 12궁 모두 빠짐없이 채워라."
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

        palaces_raw = parsed.get("palaces") or []
        palaces: list[dict] = []
        for p in palaces_raw:
            if not isinstance(p, dict):
                continue
            name_ko = str(p.get("name_ko") or "").strip()
            if not name_ko:
                continue
            palaces.append({
                "name_ko": name_ko,
                "app_title": str(p.get("app_title") or "").strip(),
                "summary": str(p.get("summary") or "").strip(),
                "love_interpretation": str(p.get("love_interpretation") or "").strip(),
                "love_tip": str(p.get("love_tip") or "").strip(),
                "keywords": [str(k).strip() for k in (p.get("keywords") or []) if k],
            })

        return {"palaces": palaces}
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
        return {
            "strengths": list(parsed.get("strengths") or []),
            "cautions": list(parsed.get("cautions") or []),
            "conversation_starters": list(parsed.get("conversation_starters") or []),
            "summary": parsed.get("summary") or None,
        }
    except Exception:
        return None


_DAILY_FORTUNE_PROMPT = (
    "당신은 사용자의 사주와 '오늘의 일진(日辰)' 신호를 바탕으로 "
    "오늘 하루의 인연운을 한국어로 짧게 써주는 다정한 데이팅 코치입니다.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 친한 코치가 옆에서 말해주는 느낌.\n"
    "- 단정·예언·저주 금지. 건강·질병·수명·이별 확정 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 아래 '오늘 신호'에 적힌 값만 근거로 쓰고, 없는 사실을 지어내지 마세요.\n"
    "- 매일 달라지는 '오늘의 십성·배지'를 자연스럽게 녹여 어제와 다른 하루 느낌을 주세요.\n"
    "- 총 2~3 문장, 150자 이내. 도입부·면책·맺음말·번호·마크다운 금지."
)

_DAILY_ACTION_PROMPT = (
    "당신은 사용자의 사주와 '오늘의 일진(日辰)' 신호를 바탕으로 "
    "오늘 어떻게 입고·어떤 태도로·어떤 마음으로 인연을 대하면 좋을지 "
    "한국어로 짧게 제안하는 다정한 데이팅 코치입니다.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말, 제안형('~해보시는 건 어떨까요'). 단정·예언 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 아래 '오늘 신호'에 적힌 값만 근거로 쓰고, 없는 사실을 지어내지 마세요.\n"
    "- 옷차림 → 태도 → 마음가짐 흐름으로, 매일 다른 느낌을 주세요.\n"
    "- 총 2~3 문장, 150자 이내. 도입부·면책·맺음말·번호·마크다운 금지."
)

_DAILY_PROMPTS: dict[str, str] = {
    "fortune": _DAILY_FORTUNE_PROMPT,
    "action_guide": _DAILY_ACTION_PROMPT,
}


def generate_daily_text(
    *,
    kind: str,
    nickname: str,
    signal_text: str,
    model: str = _MODEL,
) -> Optional[str]:
    """오늘의 인연운/행동가이드 프로즈를 LLM 으로 생성. 실패 시 None."""
    prompt = _DAILY_PROMPTS.get(kind)
    if prompt is None:
        return None
    try:
        resp = _client().responses.create(
            model=model,
            instructions=prompt,
            input=(
                f"[{nickname}님의 오늘 신호]\n{signal_text}\n\n"
                "위 신호만 근거로 오늘의 문구를 작성하십시오."
            ),
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        text = _extract_output_text(resp)
        return text or None
    except Exception:
        return None


# --- 궁합 리포트 (채팅 드로어) ---------------------------------------

_COMPAT_REPORT_SYSTEM_PROMPT = (
    "당신은 두 사용자의 사주를 비교해 '궁합 요약'을 한국어로 작성하는 "
    "다정한 데이팅 코치입니다.\n"
    "\n"
    "톤·스타일:\n"
    "- 다정한 존댓말. 단정·예언·저주 금지. 건강·수명·파탄 단정 금지.\n"
    "- 명령형 '~해라', 격식체 '~십시오', 반말 모두 금지.\n"
    "\n"
    "공통 규칙:\n"
    + _PLAIN_KOREAN_RULES +
    "- 아래 [궁합 입력]에 적힌 두 사람 정보만 근거로 쓰고, 없는 사실을 지어내지 마세요.\n"
    "- 상대를 가리킬 때는 [궁합 입력]에 적힌 이름을 그대로 사용하세요.\n"
    "- summary_lines: 정확히 3개. 순서와 역할이 정해져 있습니다.\n"
    "  1) 긍정적 케미 — 두 사람의 잘 맞는 점을 밝고 설레는 한 문장으로. (30~70자)\n"
    "  2) 주의할 점 + 멘트 예시 — 상대의 성향과 살짝 주의할 점을 짚고, 채팅에서 "
    "바로 쓸 수 있는 구체적인 멘트를 작은따옴표로 예시. (50~95자)\n"
    "  3) 채팅방 전용 실전 팁 — 오행(五行) 기운을 근거로 한 선톡·답장 코칭 한 문장. "
    "필요하면 '○○씨는 ~ 좋아한다는데 맞아요?'처럼 대화를 자연스럽게 유도하는 "
    "코칭 문장도 좋습니다. (50~95자)\n"
    "- keywords: 3개. '#' 로 시작하는 짧은 해시태그 (예: #성장하는인연, #케미좋음).\n"
    "\n"
    "반드시 아래 JSON 스키마만 출력. 다른 설명·도입부·마크다운 금지:\n"
    "{\n"
    '  "summary_lines": ["긍정적 케미 한 문장", "주의할 점+멘트 예시 한 문장", "채팅방 실전 팁 한 문장"],\n'
    '  "keywords": ["#키워드1", "#키워드2", "#키워드3"]\n'
    "}\n"
)


def generate_compatibility_report(
    *,
    score: int,
    user_a_info: dict,
    user_b_info: dict,
    model: str = _MODEL,
) -> Optional[dict[str, Any]]:
    """두 사람 사주 비교 → 궁합 요약(summary_lines 3개[긍정 케미·주의점+멘트·실전 팁] + keywords 3개). 실패 시 None."""
    nick_a = user_a_info.get("nickname") or "사용자A"
    nick_b = user_b_info.get("nickname") or "사용자B"
    user_input = "\n".join([
        "[궁합 입력]",
        f"- 궁합 점수: {score} / 100",
        f"- {nick_a}: 일주 {user_a_info.get('day_pillar')}"
        f" · 주요 오행 {user_a_info.get('dominant_element') or '미상'}"
        f" · MBTI {user_a_info.get('mbti') or '미상'}",
        f"- {nick_b}: 일주 {user_b_info.get('day_pillar')}"
        f" · 주요 오행 {user_b_info.get('dominant_element') or '미상'}"
        f" · MBTI {user_b_info.get('mbti') or '미상'}",
        "",
        f"위 정보로 두 분({nick_a}, {nick_b})의 궁합 요약 JSON 을 작성하십시오.",
    ])
    try:
        resp = _client().responses.create(
            model=model,
            instructions=_COMPAT_REPORT_SYSTEM_PROMPT,
            input=user_input,
            max_output_tokens=800,
        )
        parsed = _parse_pair_json(_extract_output_text(resp))
        if parsed is None:
            return None
        lines = [str(x).strip() for x in (parsed.get("summary_lines") or []) if str(x).strip()]
        kws = [str(x).strip() for x in (parsed.get("keywords") or []) if str(x).strip()]
        if not lines or not kws:
            return None
        return {"summary_lines": lines, "keywords": kws}
    except Exception:
        return None