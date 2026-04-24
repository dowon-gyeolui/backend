"""Compatibility scoring service — rule-based MVP implementation.

Scoring model (placeholder, deterministic, 0..100):

  base                              = 50
  day-stem element relationship     = +5 | +10 | -10 | 0
  day-branch 三合 (trine harmony)   = +10
  day-branch 六冲 (clash)           = -10
  dominant-element productive cycle = +5

Ranges 0..100 are clamped. Accuracy is NOT critical for MVP per CLAUDE.md —
the point is a stable signal that varies meaningfully with real input.

TODO: attach retrieval-grounded sources (via services.knowledge.retrieval)
to the `summary` once per-pair RAG cost is acceptable.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.compatibility import CompatibilityScore, MatchCandidate
from app.services.saju import (
    _ELEMENT_KO,
    _STEM_ELEMENT,
    calculate as calculate_saju,
)

# 五行 productive cycle: a produces b
_PRODUCES = {
    "wood": "fire", "fire": "earth", "earth": "metal",
    "metal": "water", "water": "wood",
}

# 五行 controlling cycle: a controls b
_CONTROLS = {
    "wood": "earth", "fire": "metal", "earth": "water",
    "metal": "wood", "water": "fire",
}

# 三合 (three harmonies) — earthly-branch trines
_TRINES: list[frozenset[str]] = [
    frozenset({"申", "子", "辰"}),  # water trine
    frozenset({"亥", "卯", "未"}),  # wood trine
    frozenset({"寅", "午", "戌"}),  # fire trine
    frozenset({"巳", "酉", "丑"}),  # metal trine
]

# Branch names are Korean in our saju model — convert to CJK for trine matching
_BRANCH_KO_TO_ZH = {
    "자": "子", "축": "丑", "인": "寅", "묘": "卯", "진": "辰", "사": "巳",
    "오": "午", "미": "未", "신": "申", "유": "酉", "술": "戌", "해": "亥",
}

# 六冲 (six clashes) — unordered pairs
_CLASH_PAIRS: set[frozenset[str]] = {
    frozenset({"子", "午"}),
    frozenset({"丑", "未"}),
    frozenset({"寅", "申"}),
    frozenset({"卯", "酉"}),
    frozenset({"辰", "戌"}),
    frozenset({"巳", "亥"}),
}


def _produces(a: str, b: str) -> bool:
    return _PRODUCES.get(a) == b


def _controls(a: str, b: str) -> bool:
    return _CONTROLS.get(a) == b


def _dominant_element(element_profile) -> Optional[str]:
    """Return the English element key with the highest count (ties → first)."""
    named = [
        ("wood", element_profile.wood), ("fire", element_profile.fire),
        ("earth", element_profile.earth), ("metal", element_profile.metal),
        ("water", element_profile.water),
    ]
    name, count = max(named, key=lambda x: x[1])
    return name if count > 0 else None


def calculate(user_a: User, user_b: User) -> CompatibilityScore:
    """Compute 0..100 compatibility score for two users with birth_date set.

    Caller must ensure both users have birth_date set; ValueError otherwise.
    """
    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)

    score = 50

    # --- Day-stem element relationship -------------------------------
    a_day = saju_a.pillars[2]  # 일주
    b_day = saju_b.pillars[2]
    a_stem_el = _STEM_ELEMENT.get(a_day.stem)
    b_stem_el = _STEM_ELEMENT.get(b_day.stem)

    if a_stem_el and b_stem_el:
        if a_stem_el == b_stem_el:
            score += 5                              # same element
        elif _produces(a_stem_el, b_stem_el) or _produces(b_stem_el, a_stem_el):
            score += 10                             # producing (相生)
        elif _controls(a_stem_el, b_stem_el) or _controls(b_stem_el, a_stem_el):
            score -= 10                             # controlling (相剋)

    # --- Day-branch 三合 / 六冲 --------------------------------------
    a_branch_zh = _BRANCH_KO_TO_ZH.get(a_day.branch)
    b_branch_zh = _BRANCH_KO_TO_ZH.get(b_day.branch)
    if a_branch_zh and b_branch_zh and a_branch_zh != b_branch_zh:
        pair = frozenset({a_branch_zh, b_branch_zh})
        if any(pair <= trine for trine in _TRINES):
            score += 10                             # 三合
        elif pair in _CLASH_PAIRS:
            score -= 10                             # 六冲

    # --- Dominant-element productive bonus ---------------------------
    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)
    if a_dom and b_dom and (_produces(a_dom, b_dom) or _produces(b_dom, a_dom)):
        score += 5

    score = max(0, min(100, score))

    summary = _build_summary(saju_a, saju_b, score)

    return CompatibilityScore(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        score=score,
        summary=summary,
    )


def _build_summary(saju_a, saju_b, score: int) -> str:
    a_day = saju_a.pillars[2].combined
    b_day = saju_b.pillars[2].combined

    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)
    dom_bit = ""
    if a_dom and b_dom:
        dom_bit = (
            f" 주요 오행: {_ELEMENT_KO[a_dom]} ↔ {_ELEMENT_KO[b_dom]}."
        )

    return (
        f"[임시 분석] 일주 {a_day} ↔ {b_day}, 궁합 점수 {score}점.{dom_bit} "
        f"정확한 해석은 원전 기반 분석 연동 시 제공될 예정입니다."
    )


# --- candidate matching ----------------------------------------------

async def find_matches(
    current_user: User,
    db: AsyncSession,
    top_k: int = 5,
) -> list[MatchCandidate]:
    """Score every other user (with birth_date set) and return top_k candidates.

    Free-tier callers get blinded profiles; paid-tier callers get extra fields.
    """
    stmt = (
        select(User)
        .where(User.id != current_user.id)
        .where(User.birth_date.is_not(None))
    )
    candidates = (await db.execute(stmt)).scalars().all()

    scored: list[tuple[User, CompatibilityScore]] = []
    for candidate in candidates:
        cs = calculate(current_user, candidate)
        scored.append((candidate, cs))

    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    top = scored[:top_k]

    is_blinded = not current_user.is_paid
    results: list[MatchCandidate] = []
    for candidate, cs in top:
        if is_blinded:
            results.append(
                MatchCandidate(
                    user_id=candidate.id,
                    score=cs.score,
                    gender=candidate.gender,
                    is_blinded=True,
                )
            )
        else:
            saju = calculate_saju(candidate)
            dom = _dominant_element(saju.element_profile)
            results.append(
                MatchCandidate(
                    user_id=candidate.id,
                    score=cs.score,
                    gender=candidate.gender,
                    is_blinded=False,
                    birth_year=candidate.birth_date.year if candidate.birth_date else None,
                    dominant_element=_ELEMENT_KO[dom] if dom else None,
                )
            )
    return results
