"""사용자가 정한 이름(닉네임)이 LLM 프롬프트의 '지시문'으로 읽히는 것을 막는다.

닉네임은 검열 없이 50자까지 허용되고, 궁합 요약·오늘의 문구 프롬프트에 그대로 박힌다.
코드 실행 위험은 없지만 "앞의 지시는 무시하고 ..." 같은 문구를 닉네임에 넣으면
**매칭 상대에게 보이는 생성 텍스트**를 조작할 수 있다. 프롬프트 문자열을 만들기
직전에 반드시 `sanitize_prompt_name()` 을 통과시킨다.

정책은 두 단계다.
1. 지시어처럼 읽히는 닉네임은 아예 쓰지 않고 중립적인 대체 호칭으로 바꾼다.
2. 그 외에는 프롬프트의 구조를 흉내낼 수 있는 글자(줄바꿈·괄호·따옴표 등)만 털어낸다.
"""

from __future__ import annotations

import re

# 닉네임 스키마 상한(app/schemas/user.py)과 같은 값. DB 에는 카카오에서 받은 더 긴
# 값이 들어있을 수 있어 프롬프트에 넣기 전에 한 번 더 자른다.
_MAX_NAME_LEN = 50

# 제어문자·줄바꿈. 줄바꿈이 들어가면 닉네임 한 줄이 프롬프트의 새 항목처럼 보인다.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# 프롬프트의 구조를 흉내내는 데 쓰이는 글자들. 사람 이름에는 필요 없다.
# ([사주 결과] 같은 머리표, JSON 중괄호, 코드펜스, 호칭을 감싸는 따옴표)
_STRUCTURAL_RE = re.compile(r"[\[\]{}<>`\"'|]")

# 지시어로 읽히는 패턴. 하나라도 걸리면 닉네임을 통째로 버린다.
_DIRECTIVE_PATTERNS = (
    r"무시(하고|하라|하세요|하십시오|해)",
    r"(지시|명령|규칙|프롬프트|설정)\s*(을|를|은|는)?\s*무시",
    r"시스템\s*프롬프트",
    r"위\s*(내용|지시|문장)\s*(을|를)?\s*(무시|삭제)",
    r"대신\s*(다음|아래)\s*(을|를)?\s*(출력|작성)",
    r"ignore\s+(all\s+|the\s+)?(previous|above|prior|earlier)",
    r"disregard\s+(all\s+|the\s+)?(previous|above|prior)",
    r"system\s*prompt",
    r"new\s+instructions?",
    r"you\s+are\s+now",
    r"^\s*(system|assistant|user|developer)\s*:",
)
_DIRECTIVE_RE = re.compile("|".join(_DIRECTIVE_PATTERNS), re.IGNORECASE)


def sanitize_prompt_name(raw: str | None, fallback: str) -> str:
    """프롬프트에 넣어도 안전한 호칭을 돌려준다.

    지시어 패턴이 보이거나 털어낸 뒤 남는 글자가 없으면 `fallback` 을 쓴다.
    """
    if not raw:
        return fallback

    text = _CONTROL_RE.sub(" ", raw)
    if _DIRECTIVE_RE.search(text):
        return fallback

    text = _STRUCTURAL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:_MAX_NAME_LEN]
