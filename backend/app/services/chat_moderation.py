"""Chat-message moderation — 3-layer pipeline.

Why three layers:
  Layer 1 (regex)        — Cheap, deterministic, runs in ~0.5ms. Catches
                           the highest-priority class of abuse on a dating
                           app: trying to take the conversation off-platform
                           by leaking phone numbers / KakaoTalk IDs / URLs.
                           Off-platform leaks are revenue-killers (no
                           subscription needed once they have your number).
  Layer 2 (profanity)    — Tiny Korean profanity dictionary + simple
                           obfuscation handling (whitespace, repeated
                           chars). Catches the low-hanging insults instantly.
  Layer 3 (OpenAI Mod.)  — Free OpenAI Moderation API for nuanced cases
                           (harassment, threats, sexual content, hate).
                           Multilingual / context-aware, ~200~400ms latency.

A message that fails layers 1 or 2 is **hard-blocked** before reaching the
DB. Layer 3 flags are also hard-blocked but logged with the category so
we can iterate on thresholds.

Each block increments the user's strike counter. Frontend handles display
(error toast + warning copy).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Optional

logger = logging.getLogger(__name__)


ViolationKind = Literal[
    "contact_leak",  # 외부 연락처 유출 시도
    "profanity",     # 욕설/비속어
    "harassment",    # 괴롭힘 / 위협
    "sexual",        # 성적 콘텐츠
    "spam",          # 광고 / 스팸
    "other",         # 기타 OpenAI 플래그
]


@dataclass
class ChatModerationResult:
    ok: bool
    kind: Optional[ViolationKind] = None
    reason: Optional[str] = None     # 한국어 사용자용 메시지
    detail: Optional[str] = None     # 로그용

    @classmethod
    def passed(cls) -> "ChatModerationResult":
        return cls(ok=True)


# --- Layer 1: contact-info leak detection --------------------------------

# Korean mobile pattern: 010 / 011 / 016~019, with optional separators.
_PHONE_RE = re.compile(
    r"01[016789][\s\-.·]?\d{3,4}[\s\-.·]?\d{4}"
)

# "카톡 아이디 xxx" / "카카오 ID xxx" / "오픈채팅" — common phrasings.
_KAKAO_RE = re.compile(
    r"(카[카오]?(톡)?|kakao|오픈\s*채팅)\s*(아이디|아디|id|ID)?\s*[:：=]?\s*[A-Za-z0-9_.\-]{3,}",
    re.IGNORECASE,
)

# 인스타 / 텔레그램 / 디스코드 — same intent
_OTHER_HANDLE_RE = re.compile(
    r"(insta(gram)?|인스타|텔레(그램)?|telegram|디스코드|discord|line|라인|snap)\s*"
    r"(아이디|id|ID|@)?\s*[:：=]?\s*[A-Za-z0-9_.\-]{3,}",
    re.IGNORECASE,
)

# Bare URLs / shorteners
_URL_RE = re.compile(
    r"(https?://|www\.|t\.me/|bit\.ly/|kko\.to/|naver\.me/|me2\.do/|shorturl\.|tinyurl\.)"
    r"|[a-z0-9-]+\.(com|net|kr|me|co|io|app|page|link)\b",
    re.IGNORECASE,
)


def _check_contact_leak(content: str) -> Optional[ChatModerationResult]:
    if _PHONE_RE.search(content):
        return ChatModerationResult(
            ok=False,
            kind="contact_leak",
            reason="외부 연락처(전화번호)는 채팅에서 공유할 수 없어요.",
            detail="phone",
        )
    if _KAKAO_RE.search(content):
        return ChatModerationResult(
            ok=False,
            kind="contact_leak",
            reason="외부 연락처(카카오톡 ID)는 채팅에서 공유할 수 없어요.",
            detail="kakao",
        )
    if _OTHER_HANDLE_RE.search(content):
        return ChatModerationResult(
            ok=False,
            kind="contact_leak",
            reason="외부 SNS·메신저 아이디는 채팅에서 공유할 수 없어요.",
            detail="other_handle",
        )
    if _URL_RE.search(content):
        return ChatModerationResult(
            ok=False,
            kind="contact_leak",
            reason="링크는 채팅에서 공유할 수 없어요.",
            detail="url",
        )
    return None


# --- Layer 2: simple Korean profanity dictionary -------------------------

# Small starter set — focus on the most-used words + their common
# obfuscations (consonant-only, vowel insertion). For a real product
# you'd grow this list from your moderation logs.
_PROFANITY_WORDS: set[str] = {
    "씨발", "ㅅㅂ", "시발", "씨바", "씨팔", "ㅆㅂ",
    "병신", "ㅂㅅ", "븅신",
    "개새끼", "개색기", "개시키", "개섹기",
    "좆", "좆까",
    "닥쳐", "꺼져",
    "지랄", "ㅈㄹ",
    "미친놈", "미친년", "미친새끼",
    "느금마", "니애미", "엠창",
    "fuck", "shit", "bitch", "asshole",
}


def _normalize_for_profanity(text: str) -> str:
    """Strip whitespace + punctuation so 'ㅅ ㅂ' or 'ㅅ.ㅂ' still hits."""
    return re.sub(r"[\s\W_]+", "", text.lower())


def _check_profanity(content: str) -> Optional[ChatModerationResult]:
    normalized = _normalize_for_profanity(content)
    for word in _PROFANITY_WORDS:
        if word in normalized:
            return ChatModerationResult(
                ok=False,
                kind="profanity",
                reason="욕설·비속어가 감지되어 메시지를 보낼 수 없어요.",
                detail=f"word={word}",
            )
    return None


# --- Layer 3: OpenAI Moderation API --------------------------------------

@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=api_key)


# Map OpenAI moderation categories to our user-facing copy + ViolationKind.
_OPENAI_CATEGORY_MAP: dict[str, tuple[ViolationKind, str]] = {
    "harassment": ("harassment", "다른 사용자를 괴롭히는 내용은 보낼 수 없어요."),
    "harassment/threatening": ("harassment", "위협하는 내용은 보낼 수 없어요."),
    "hate": ("harassment", "혐오 표현은 보낼 수 없어요."),
    "hate/threatening": ("harassment", "혐오·위협 표현은 보낼 수 없어요."),
    "self-harm": ("other", "자해 관련 내용은 보낼 수 없어요."),
    "self-harm/intent": ("other", "자해 관련 내용은 보낼 수 없어요."),
    "self-harm/instructions": ("other", "자해 관련 내용은 보낼 수 없어요."),
    "sexual": ("sexual", "성적인 내용은 보낼 수 없어요."),
    "sexual/minors": ("sexual", "부적절한 내용이 감지됐어요."),
    "violence": ("harassment", "폭력적인 내용은 보낼 수 없어요."),
    "violence/graphic": ("harassment", "폭력적인 내용은 보낼 수 없어요."),
}


def _check_openai_moderation(content: str) -> Optional[ChatModerationResult]:
    if not os.environ.get("OPENAI_API_KEY"):
        return None  # graceful degrade if key isn't set
    try:
        resp = _openai_client().moderations.create(
            model="omni-moderation-latest",
            input=content,
        )
    except Exception as e:
        logger.exception("OpenAI moderation call failed: %s", e)
        return None  # don't fail send on a transient API issue

    result = resp.results[0]
    if not result.flagged:
        return None

    # Pick the FIRST flagged category we have a mapping for, in priority
    # order matching _OPENAI_CATEGORY_MAP keys.
    flagged_cats = result.categories
    flagged_dict = (
        flagged_cats.model_dump()
        if hasattr(flagged_cats, "model_dump")
        else dict(flagged_cats)
    )
    for cat_name, (kind, reason) in _OPENAI_CATEGORY_MAP.items():
        if flagged_dict.get(cat_name):
            return ChatModerationResult(
                ok=False,
                kind=kind,
                reason=reason,
                detail=f"openai_cat={cat_name}",
            )

    # Flagged but no specific category we want to surface — generic block.
    return ChatModerationResult(
        ok=False,
        kind="other",
        reason="부적절한 내용이 감지됐어요.",
        detail="openai_flagged_generic",
    )


# --- Top-level entry point -----------------------------------------------

def moderate_chat_message(content: str) -> ChatModerationResult:
    """Run all three layers in cheap-to-expensive order.

    Empty / whitespace-only messages pass through (the upstream router
    already rejects empty content via Pydantic min_length=1).
    """
    text = (content or "").strip()
    if not text:
        return ChatModerationResult.passed()

    # Layer 1
    leak = _check_contact_leak(text)
    if leak is not None:
        return leak

    # Layer 2
    profanity = _check_profanity(text)
    if profanity is not None:
        return profanity

    # Layer 3 — only reach if 1 + 2 passed. Costs ~200ms but the user is
    # already typing, so this fires after they tap "보내기".
    openai = _check_openai_moderation(text)
    if openai is not None:
        return openai

    return ChatModerationResult.passed()