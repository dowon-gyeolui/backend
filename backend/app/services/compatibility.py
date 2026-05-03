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

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_match import DailyMatch
from app.models.user import User
from app.schemas.compatibility import (
    CompatibilityReport,
    CompatibilityScore,
    DailyMatchPack,
    DailyMatchSlot,
    DateRecommendation,
    DateSpot,
    DestinyAnalysis,
    HistoryMatchEntry,
    MatchCandidate,
)
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


# --- 운명 분석 리포트 --------------------------------------------------

def _name_or_default(user: User) -> str:
    return user.nickname or f"사용자 {user.id}"


def _branch_relation(a_branch_ko: Optional[str], b_branch_ko: Optional[str]) -> str:
    """Return 'trine' | 'clash' | 'same' | 'neutral' for two day-branches."""
    a = _BRANCH_KO_TO_ZH.get(a_branch_ko or "")
    b = _BRANCH_KO_TO_ZH.get(b_branch_ko or "")
    if not a or not b:
        return "neutral"
    if a == b:
        return "same"
    pair = frozenset({a, b})
    if any(pair <= trine for trine in _TRINES):
        return "trine"
    if pair in _CLASH_PAIRS:
        return "clash"
    return "neutral"


def _stem_relation(a_stem_el: Optional[str], b_stem_el: Optional[str]) -> str:
    """Return 'same' | 'produce' | 'control' | 'neutral'."""
    if not a_stem_el or not b_stem_el:
        return "neutral"
    if a_stem_el == b_stem_el:
        return "same"
    if _produces(a_stem_el, b_stem_el) or _produces(b_stem_el, a_stem_el):
        return "produce"
    if _controls(a_stem_el, b_stem_el) or _controls(b_stem_el, a_stem_el):
        return "control"
    return "neutral"


def _build_synergy_line(
    name_a: str, name_b: str,
    a_dom: Optional[str], b_dom: Optional[str],
    stem_rel: str, branch_rel: str,
) -> str:
    """First bullet — what makes the pairing work."""
    a_ko = _ELEMENT_KO.get(a_dom or "") if a_dom else None
    b_ko = _ELEMENT_KO.get(b_dom or "") if b_dom else None

    if a_dom and b_dom and a_dom != b_dom and stem_rel == "produce":
        return (
            f"{name_a}님과 {name_b}님은 서로 "
            f"{a_ko}의 기운과 {b_ko}의 기운을 보충하며 "
            f"장기 연애 가능성이 높은 궁합입니다."
        )
    if a_dom and b_dom and a_dom == b_dom:
        return (
            f"두 분 모두 {a_ko}의 기운이 두드러져 "
            f"공통 관심사와 가치관 위에서 빠르게 가까워지는 궁합입니다."
        )
    if branch_rel == "trine":
        return (
            f"{name_a}님과 {name_b}님의 일주가 三合(삼합) 관계로, "
            f"안정적이고 오래 가는 인연이 될 가능성이 높습니다."
        )
    if stem_rel == "control" and a_ko and b_ko:
        return (
            f"{a_ko}와 {b_ko}의 기운이 서로 견제하지만, "
            f"그만큼 서로의 부족한 부분을 채워줄 수 있는 관계입니다."
        )
    return (
        f"{name_a}님과 {name_b}님은 서로 다른 기운을 가져 "
        f"새로운 자극과 변화의 기회가 많은 궁합입니다."
    )


def _build_caution_line(
    a_dom: Optional[str], b_dom: Optional[str],
    stem_rel: str, branch_rel: str,
) -> str:
    """Second bullet — what to watch out for."""
    a_ko = _ELEMENT_KO.get(a_dom or "") if a_dom else None
    b_ko = _ELEMENT_KO.get(b_dom or "") if b_dom else None

    if branch_rel == "clash":
        return (
            "다만 일주의 지지가 六冲(육충) 관계라 "
            "감정이 격해지면 충돌이 잦을 수 있어요. "
            "갈등 시 한 박자 쉬어가는 대화가 중요합니다."
        )
    if stem_rel == "control" and a_ko and b_ko:
        return (
            f"반대로 {a_ko}와 {b_ko}의 기운이 부딪쳐 가끔 충돌할 가능성이 있습니다. "
            "연애 전 가치관·생활 패턴을 확실히 맞춰보고 가세요."
        )
    if a_dom == "fire" or b_dom == "fire":
        return (
            "다만 화(火)의 기운이 강해 감정 표현이 직설적일 수 있어요. "
            "표현 강도를 서로 맞춰가는 대화가 필요합니다."
        )
    return (
        "다만 처음에는 표현 방식이 달라 어색할 수 있으니, "
        "초반 한 달 동안 솔직한 대화로 서로의 페이스를 맞춰보세요."
    )


_SCORE_TAG: list[tuple[int, str]] = [
    (85, "#찰떡궁합"),
    (70, "#호감궁합"),
    (50, "#노력형궁합"),
    (0,  "#성장궁합"),
]


def _score_keyword(score: int) -> str:
    for threshold, tag in _SCORE_TAG:
        if score >= threshold:
            return tag
    return "#성장궁합"


def _theme_keyword(
    stem_rel: str, branch_rel: str,
    a_dom: Optional[str], b_dom: Optional[str],
) -> str:
    if branch_rel == "trine":
        return "#오래가는_인연"
    if branch_rel == "clash":
        return "#서로_배워가는_사이"
    if stem_rel == "produce":
        return "#솔직한_대화"
    if stem_rel == "same" and a_dom == b_dom:
        return "#닮은꼴"
    if stem_rel == "control":
        return "#균형_맞추기"
    return "#새로운_자극"


def build_report(user_a: User, user_b: User) -> CompatibilityReport:
    """Build a 운명 분석 리포트 for the chat drawer.

    Reuses the same metric sources as `calculate()` but renders them as
    natural-language bullets + hashtag chips instead of a raw score.
    """
    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)

    a_day = saju_a.pillars[2]
    b_day = saju_b.pillars[2]
    a_stem_el = _STEM_ELEMENT.get(a_day.stem)
    b_stem_el = _STEM_ELEMENT.get(b_day.stem)
    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)

    stem_rel = _stem_relation(a_stem_el, b_stem_el)
    branch_rel = _branch_relation(a_day.branch, b_day.branch)

    score_obj = calculate(user_a, user_b)

    name_a = _name_or_default(user_a)
    name_b = _name_or_default(user_b)

    synergy = _build_synergy_line(name_a, name_b, a_dom, b_dom, stem_rel, branch_rel)
    caution = _build_caution_line(a_dom, b_dom, stem_rel, branch_rel)

    # Keyword 1 — counterpart's dominant element (= "what their saju brings").
    if b_dom:
        elem_keyword = f"#{_ELEMENT_KO[b_dom]}의_기운"
    elif a_dom:
        elem_keyword = f"#{_ELEMENT_KO[a_dom]}의_기운"
    else:
        elem_keyword = "#오행균형"

    keywords = [
        elem_keyword,
        _score_keyword(score_obj.score),
        _theme_keyword(stem_rel, branch_rel, a_dom, b_dom),
    ]

    return CompatibilityReport(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        nickname_a=user_a.nickname,
        nickname_b=user_b.nickname,
        score=score_obj.score,
        summary_lines=[synergy, caution],
        keywords=keywords,
    )


# --- candidate matching ----------------------------------------------

def _compute_age(birth_date: Optional[date]) -> Optional[int]:
    """International age as of today."""
    if birth_date is None:
        return None
    today = date.today()
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


async def find_matches(
    current_user: User,
    db: AsyncSession,
    top_k: int = 5,
) -> list[MatchCandidate]:
    """Score every other user (with birth_date set) and return top_k candidates.

    Free-tier callers get blinded profiles; paid-tier callers get extra fields.
    Profile card fields (nickname, age, gender) are always visible so the
    UI can render a usable card even under the blind policy.
    """
    stmt = (
        select(User)
        .where(User.id != current_user.id)
        .where(User.birth_date.is_not(None))
        # 사진 없는 사용자는 매칭 풀에서 제외 (얼굴 사진 등록이 시작
        # 조건). photo_url 은 갤러리의 primary 사진 url 미러링.
        .where(User.photo_url.is_not(None))
    )
    # Default dating-app behaviour: prefer opposite-gender candidates.
    # When the current user has no gender set we don't filter — show all.
    if current_user.gender == "male":
        stmt = stmt.where(User.gender == "female")
    elif current_user.gender == "female":
        stmt = stmt.where(User.gender == "male")

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
        card = MatchCandidate(
            user_id=candidate.id,
            score=cs.score,
            nickname=candidate.nickname,
            age=_compute_age(candidate.birth_date),
            gender=candidate.gender,
            is_blinded=is_blinded,
            # Photo is always returned so the UI can render a blurred
            # preview ("see what you're missing"). The frontend applies
            # blur whenever is_blinded is True.
            photo_url=candidate.photo_url,
            is_face_verified=await _is_primary_face_verified(candidate, db),
        )
        if not is_blinded:
            saju = calculate_saju(candidate)
            dom = _dominant_element(saju.element_profile)
            card.birth_year = (
                candidate.birth_date.year if candidate.birth_date else None
            )
            card.dominant_element = _ELEMENT_KO[dom] if dom else None
            card.mbti = candidate.mbti
        results.append(card)
    return results


async def _is_primary_face_verified(user: User, db: AsyncSession) -> bool:
    """후보의 메인 사진이 strict face check 를 통과했는지.

    매칭 카드의 ZAMI 공식 인증 뱃지 표시용. user_photos 테이블에서
    is_primary=True 인 row 의 is_face_verified 플래그 확인.
    """
    from app.models.photo import UserPhoto
    primary = (
        await db.execute(
            select(UserPhoto)
            .where(UserPhoto.user_id == user.id)
            .where(UserPhoto.is_primary.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    return bool(primary and primary.is_face_verified)


# --- Date recommendation ----------------------------------------------

def build_date_recommendation(user_a: User, user_b: User) -> DateRecommendation:
    """Build a DateRecommendation for a paid pair.

    Pulls saju metrics for both users, calls the LLM with their dominant
    elements + day pillars + MBTI, and returns the structured spots.
    On LLM failure returns the same shape with status='pending' so the
    UI can render a friendly fallback.
    """
    from app.services.llm.interpret import generate_date_recommendation

    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)
    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)
    score_obj = calculate(user_a, user_b)

    sections = generate_date_recommendation(
        score=score_obj.score,
        user_a_info={
            "nickname": user_a.nickname,
            "day_pillar": saju_a.pillars[2].combined,
            "dominant_element": _ELEMENT_KO.get(a_dom or ""),
            "gender": user_a.gender,
            "mbti": user_a.mbti,
        },
        user_b_info={
            "nickname": user_b.nickname,
            "day_pillar": saju_b.pillars[2].combined,
            "dominant_element": _ELEMENT_KO.get(b_dom or ""),
            "gender": user_b.gender,
            "mbti": user_b.mbti,
        },
    )

    out = DateRecommendation(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        nickname_a=user_a.nickname,
        nickname_b=user_b.nickname,
        score=score_obj.score,
    )
    if sections is None:
        return out

    out.overview = sections.get("overview", "")
    out.spots = [
        DateSpot(title=s["title"], description=s["description"])
        for s in sections.get("spots", [])
    ]
    if out.overview or out.spots:
        out.interpretation_status = "ready"
    return out



def build_destiny_analysis(user_a: User, user_b: User) -> DestinyAnalysis:
    """운명의 실타래 — 두 사람 사주를 직접 비교한 5 섹션 풀이."""
    from app.services.llm.interpret import generate_destiny_analysis

    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)
    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)
    score_obj = calculate(user_a, user_b)

    a_day = saju_a.pillars[2]
    b_day = saju_b.pillars[2]
    a_stem_el = _STEM_ELEMENT.get(a_day.stem)
    b_stem_el = _STEM_ELEMENT.get(b_day.stem)

    sections = generate_destiny_analysis(
        score=score_obj.score,
        user_a_info={
            "nickname": user_a.nickname,
            "day_pillar": a_day.combined,
            "day_stem_element": _ELEMENT_KO.get(a_stem_el or ""),
            "dominant_element": _ELEMENT_KO.get(a_dom or ""),
            "gender": user_a.gender,
            "mbti": user_a.mbti,
        },
        user_b_info={
            "nickname": user_b.nickname,
            "day_pillar": b_day.combined,
            "day_stem_element": _ELEMENT_KO.get(b_stem_el or ""),
            "dominant_element": _ELEMENT_KO.get(b_dom or ""),
            "gender": user_b.gender,
            "mbti": user_b.mbti,
        },
    )

    out = DestinyAnalysis(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        nickname_a=user_a.nickname,
        nickname_b=user_b.nickname,
        score=score_obj.score,
    )
    if sections is None:
        return out

    out.intro = sections.get("intro", "")
    out.personality = sections.get("personality", "")
    out.love_style = sections.get("love_style", "")
    out.caution = sections.get("caution", "")
    out.longterm = sections.get("longterm", "")
    if any([out.intro, out.personality, out.love_style, out.caution, out.longterm]):
        out.interpretation_status = "ready"
    return out


# --- Daily 4-slot match assignment ----------------------------------------
#
# Cycle = 96 hours, anchored to the most recent **정오 12:00 KST** so all
# users in the same cycle window share the same `assigned_at` (deterministic,
# fair, and easy to reason about — "오늘 정오 카드"). A "pack" is the set of
# 4 DailyMatch rows sharing one `assigned_at`. Slot policy:
#
#   slot 0 → 사주 무료. Unlocked at assigned_at (= 사이클 시작 정오 12:00).
#   slot 1 → 자미두수 유료. Unlocked at assigned_at, photo blinded if !paid.
#   slot 2 → 사주 무료. Unlocked at assigned_at + 72h (= 3일 후 정오 12:00).
#   slot 3 → 자미두수 유료. Unlocked at assigned_at + 72h, blinded if !paid.
#
# 사용자 로그인 시간이 아니라 정오 기준이라, 하루 동안 여러 번 로그인해도
# 같은 카운트다운(타이머)을 보고, 다른 사용자도 같은 시각에 unlock 됨.
#
# A new pack is generated on the first /compatibility/today call once the
# previous pack's assigned_at + 96h has passed (= 잠금 해제 후 24시간 동안
# 4 장 모두 열린 상태로 볼 수 있는 버퍼). Older packs stay in
# `daily_matches` to feed the cumulative history list.

# Korea Standard Time — assigned_at 을 KST 정오에 맞추기 위해 사용.
_KST = timezone(timedelta(hours=9))

CYCLE_HOURS = 96
LOCK_HOURS = 72
SLOT_COUNT = 4
SLOT_BASIS: dict[int, Literal["saju", "jamidusu"]] = {
    0: "saju",
    1: "jamidusu",
    2: "saju",
    3: "jamidusu",
}
PAID_SLOTS = {1, 3}
LOCKED_INITIALLY_SLOTS = {2, 3}


def _snap_to_noon_kst(dt: datetime) -> datetime:
    """주어진 UTC 시각을 KST 정오(12:00)로 round-down 해서 UTC 로 반환.

    예: KST 2026-04-30 14:32 → KST 2026-04-30 12:00 → UTC 03:00
        KST 2026-04-30 09:15 → KST 2026-04-29 12:00 → UTC 04-29 03:00
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    kst_dt = dt.astimezone(_KST)
    noon_kst = kst_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if noon_kst > kst_dt:
        noon_kst -= timedelta(days=1)
    return noon_kst.astimezone(timezone.utc)


def _current_cycle_anchor() -> datetime:
    """현재 시점의 cycle anchor — 가장 최근 KST 정오."""
    return _snap_to_noon_kst(datetime.now(timezone.utc))


def _slot_unlock_at(assigned_at: datetime, slot_index: int) -> datetime:
    """Compute slot unlock time anchored to noon, not raw assigned_at.

    DB 에는 옛 코드 시절 (사용자 로그인 시각) 으로 저장된 row 가 그대로
    살아있을 수 있어, 단순히 `assigned_at + LOCK_HOURS` 로 계산하면
    잠금 해제 시각이 noon 이 아닌 임의 시각이 됨. 이 함수는 stored
    assigned_at 을 항상 가장 최근 noon 으로 snap 한 뒤 LOCK_HOURS 를
    더하므로, 옛 row 도 새 row 도 모두 KST 정오 단위로 정렬된 unlock
    시각을 갖게 된다.
    """
    anchor = _snap_to_noon_kst(assigned_at)
    if slot_index in LOCKED_INITIALLY_SLOTS:
        return anchor + timedelta(hours=LOCK_HOURS)
    return anchor


def _build_card_for(
    candidate: User,
    *,
    score: int,
    viewer_is_paid: bool,
    is_paid_slot: bool,
    is_face_verified: bool = False,
) -> MatchCandidate:
    """Build a MatchCandidate respecting per-slot photo policy.

    Free slots (0,2): always reveal photo + extras.
    Paid slots (1,3): photo + extras only if the viewer has paid.
    """
    is_blinded = is_paid_slot and not viewer_is_paid

    card = MatchCandidate(
        user_id=candidate.id,
        score=score,
        nickname=candidate.nickname,
        age=_compute_age(candidate.birth_date),
        gender=candidate.gender,
        is_blinded=is_blinded,
        photo_url=candidate.photo_url,
        is_face_verified=is_face_verified,
    )
    if not is_blinded:
        try:
            saju = calculate_saju(candidate)
            dom = _dominant_element(saju.element_profile)
            card.birth_year = (
                candidate.birth_date.year if candidate.birth_date else None
            )
            card.dominant_element = _ELEMENT_KO[dom] if dom else None
            card.mbti = candidate.mbti
        except Exception:
            # Saju calc may fail for malformed birth data — keep the card
            # usable with just the always-visible fields.
            pass
    return card


async def _latest_pack(
    user_id: int, db: AsyncSession,
) -> list[DailyMatch]:
    """Return the most recent cycle's 4 rows for the user (newest assigned_at).

    Empty list when the user has never been assigned a pack.
    """
    latest_assigned_at = (
        await db.execute(
            select(DailyMatch.assigned_at)
            .where(DailyMatch.user_id == user_id)
            .order_by(desc(DailyMatch.assigned_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    if latest_assigned_at is None:
        return []

    rows = (
        await db.execute(
            select(DailyMatch)
            .where(DailyMatch.user_id == user_id)
            .where(DailyMatch.assigned_at == latest_assigned_at)
            .order_by(DailyMatch.slot_index)
        )
    ).scalars().all()
    return list(rows)


async def _candidate_pool(
    current_user: User, db: AsyncSession,
) -> list[User]:
    """Eligible candidates for assignment — same gender filter as live matches.

    얼굴 사진 등록 안 한 사용자는 매칭 풀에서 제외 (option B 정책).
    photo_url 은 갤러리 primary 사진 url 미러링이므로 NOT NULL =
    "최소 한 장의 검증된 얼굴 사진을 등록함" 의미.
    """
    stmt = (
        select(User)
        .where(User.id != current_user.id)
        .where(User.birth_date.is_not(None))
        .where(User.photo_url.is_not(None))
    )
    if current_user.gender == "male":
        stmt = stmt.where(User.gender == "female")
    elif current_user.gender == "female":
        stmt = stmt.where(User.gender == "male")
    return list((await db.execute(stmt)).scalars().all())


async def _create_new_pack(
    current_user: User, db: AsyncSession,
) -> list[DailyMatch]:
    """Pick top-4 compat candidates and persist them as a new cycle.

    Both saju (slot 0,2) and jamidusu (slot 1,3) currently use the same
    rule-based compat score — the basis label is informational so the UI
    can call out which engine drove the pick. When a real jamidusu-based
    score lands later, this is where it'd plug in.
    """
    pool = await _candidate_pool(current_user, db)
    scored = sorted(
        ((c, calculate(current_user, c).score) for c in pool),
        key=lambda pair: pair[1],
        reverse=True,
    )
    top = scored[:SLOT_COUNT]

    # Snap assigned_at to the most recent KST 자정 so all 4 rows share a
    # deterministic, all-users-aligned cycle key. Two users who first hit
    # /today within the same calendar day (KST) will see exactly the
    # same countdown timer, and slot 2/3 unlocks at the same wall-clock
    # moment for everyone — easier to reason about than per-user windows.
    cycle_anchor = _current_cycle_anchor()
    rows: list[DailyMatch] = []
    for slot_index in range(SLOT_COUNT):
        if slot_index >= len(top):
            # Not enough candidates yet — skip the slot. Pack will be
            # short until more users sign up; UI handles empty slots.
            continue
        candidate, _score = top[slot_index]
        rows.append(
            DailyMatch(
                user_id=current_user.id,
                candidate_id=candidate.id,
                slot_index=slot_index,
                assigned_at=cycle_anchor,
            )
        )

    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


async def get_or_assign_today_pack(
    current_user: User, db: AsyncSession,
) -> DailyMatchPack:
    """Return the user's current 4-card pack, generating a new cycle if
    the previous one has expired (>= CYCLE_HOURS old) or never existed.
    """
    rows = await _latest_pack(current_user.id, db)

    needs_new = False
    if not rows:
        needs_new = True
    else:
        # 사이클 만료 판단도 noon-snap 기준으로. DB 의 raw assigned_at
        # 이 옛 코드로 인해 noon 이 아닐 수 있어, 그대로 쓰면 만료
        # 시각이 임의 시각이 되어 새 사이클이 noon 이 아닌 시각에
        # 시작될 수 있음. snap 으로 일관된 noon 정렬 보장.
        anchor = _snap_to_noon_kst(rows[0].assigned_at)
        age = datetime.now(timezone.utc) - anchor
        if age >= timedelta(hours=CYCLE_HOURS):
            needs_new = True

    if needs_new:
        rows = await _create_new_pack(current_user, db)

    return await _materialize_pack(current_user, rows, db)


async def _materialize_pack(
    current_user: User, rows: list[DailyMatch], db: AsyncSession,
) -> DailyMatchPack:
    """Hydrate stored DailyMatch rows into the response shape."""
    if not rows:
        # Pool too small to assign anything — return an empty pack with a
        # placeholder cycle window so the client can render "no matches yet".
        now = datetime.now(timezone.utc)
        return DailyMatchPack(
            assigned_at=now,
            next_cycle_at=now + timedelta(hours=CYCLE_HOURS),
            slots=[],
        )

    now = datetime.now(timezone.utc)
    # 클라이언트가 표시할 anchor + next_cycle 도 noon 기준으로 일관되게.
    # stored assigned_at 이 옛 row 라 noon 이 아니면 snap 해서 보정.
    raw_assigned_at = rows[0].assigned_at
    assigned_at = _snap_to_noon_kst(raw_assigned_at)
    next_cycle_at = assigned_at + timedelta(hours=CYCLE_HOURS)

    slots: list[DailyMatchSlot] = []
    for row in rows:
        candidate_user = await db.get(User, row.candidate_id)
        if candidate_user is None:
            continue  # candidate was deleted — skip the slot

        score = calculate(current_user, candidate_user).score
        is_paid_slot = row.slot_index in PAID_SLOTS
        unlock_at = _slot_unlock_at(assigned_at, row.slot_index)
        is_locked = now < unlock_at

        # Hide candidate identity entirely while time-locked — UI shows
        # silhouette + countdown only.
        if is_locked:
            blank_candidate = MatchCandidate(
                user_id=candidate_user.id,
                score=score,
                nickname=None,
                age=None,
                gender=None,
                is_blinded=True,
                photo_url=None,
            )
            card = blank_candidate
        else:
            card = _build_card_for(
                candidate_user,
                score=score,
                viewer_is_paid=current_user.is_paid,
                is_paid_slot=is_paid_slot,
                is_face_verified=await _is_primary_face_verified(candidate_user, db),
            )

        slots.append(
            DailyMatchSlot(
                slot_index=row.slot_index,
                match_basis=SLOT_BASIS[row.slot_index],
                candidate=card,
                assigned_at=assigned_at,
                unlock_at=unlock_at,
                is_locked=is_locked,
                requires_payment=is_paid_slot,
            )
        )

    slots.sort(key=lambda s: s.slot_index)
    return DailyMatchPack(
        assigned_at=assigned_at,
        next_cycle_at=next_cycle_at,
        slots=slots,
    )


async def list_match_history(
    current_user: User, db: AsyncSession,
) -> list[HistoryMatchEntry]:
    """Cumulative match history — every candidate ever assigned to the user.

    Deduplicated by candidate_id (most recent slot wins). Entries beyond
    the current cycle stay listed; locks recompute against `now` so an
    old slot 2/3 from yesterday's pack will show as unlocked today.
    """
    rows = (
        await db.execute(
            select(DailyMatch)
            .where(DailyMatch.user_id == current_user.id)
            .order_by(desc(DailyMatch.assigned_at), DailyMatch.slot_index)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    seen: set[int] = set()
    entries: list[HistoryMatchEntry] = []
    for row in rows:
        if row.candidate_id in seen:
            continue
        seen.add(row.candidate_id)

        candidate_user = await db.get(User, row.candidate_id)
        if candidate_user is None:
            continue

        score = calculate(current_user, candidate_user).score
        is_paid_slot = row.slot_index in PAID_SLOTS
        unlock_at = _slot_unlock_at(row.assigned_at, row.slot_index)
        is_locked = now < unlock_at

        if is_locked:
            card = MatchCandidate(
                user_id=candidate_user.id,
                score=score,
                nickname=None,
                age=None,
                gender=None,
                is_blinded=True,
                photo_url=None,
            )
        else:
            card = _build_card_for(
                candidate_user,
                score=score,
                viewer_is_paid=current_user.is_paid,
                is_paid_slot=is_paid_slot,
                is_face_verified=await _is_primary_face_verified(candidate_user, db),
            )

        entries.append(
            HistoryMatchEntry(
                candidate=card,
                slot_index=row.slot_index,
                match_basis=SLOT_BASIS[row.slot_index],
                assigned_at=row.assigned_at,
                unlock_at=unlock_at,
                is_locked=is_locked,
                requires_payment=is_paid_slot,
            )
        )
    return entries
