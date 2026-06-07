"""궁합 점수 계산 서비스 — rule-based MVP 구현.

점수 모델(결정론적, 0~100, clamp):
  base                              = 50
  일간(日干) 오행 관계               = +5 | +10 | -10 | 0
  일지(日支) 三合(삼합)              = +10
  일지(日支) 六沖(육충)              = -10
  주도 오행 상생 관계                = +5

CLAUDE.md 의 MVP 원칙대로 정확도보다 입력에 따라 의미 있게 변하는
안정 시그널을 제공하는 것이 목표. 추후 RAG 기반 근거(인용 출처)
부착이 TODO.

동시에 궁합 리포트(AI) 생성, 매칭 카드 후보 선별 등 매칭 도메인의
상위 로직도 이 모듈에서 다룬다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.compatibility import (
    CompatibilityReport,
    CompatibilityScore,
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

    # AI 우선 — 실패 시 위 규칙 기반 summary_lines/keywords 로 fallback.
    ai = None
    try:
        from app.services.llm.interpret import generate_compatibility_report

        ai = generate_compatibility_report(
            score=score_obj.score,
            user_a_info={
                "nickname": user_a.nickname,
                "day_pillar": a_day.combined,
                "dominant_element": _ELEMENT_KO.get(a_dom or ""),
                "mbti": user_a.mbti,
            },
            user_b_info={
                "nickname": user_b.nickname,
                "day_pillar": b_day.combined,
                "dominant_element": _ELEMENT_KO.get(b_dom or ""),
                "mbti": user_b.mbti,
            },
        )
    except Exception:
        ai = None

    return CompatibilityReport(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        nickname_a=user_a.nickname,
        nickname_b=user_b.nickname,
        score=score_obj.score,
        summary_lines=ai["summary_lines"] if ai else [synergy, caution],
        keywords=ai["keywords"] if ai else keywords,
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


# Korea Standard Time — KST 자정(00:00) 기준 계산에 사용.
_KST = timezone(timedelta(hours=9))


def _snap_to_midnight_kst(dt: datetime) -> datetime:
    """주어진 UTC 시각을 KST 자정(00:00)으로 round-down 해서 UTC 로 반환.

    예: KST 2026-05-01 11:30 → KST 2026-05-01 00:00 → UTC 04-30 15:00
        KST 2026-05-01 15:30 → KST 2026-05-01 00:00 → UTC 04-30 15:00
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    kst_dt = dt.astimezone(_KST)
    midnight_kst = kst_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_kst.astimezone(timezone.utc)


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


