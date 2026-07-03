"""궁합 점수 계산 · AI 리포트 생성 · 매칭 카드 후보 선별을 담당하는 매칭 도메인 핵심 서비스."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.block import UserBlock
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

_PRODUCES = {
    "wood": "fire", "fire": "earth", "earth": "metal",
    "metal": "water", "water": "wood",
}

_CONTROLS = {
    "wood": "earth", "fire": "metal", "earth": "water",
    "metal": "wood", "water": "fire",
}

_TRINES: list[frozenset[str]] = [
    frozenset({"申", "子", "辰"}),
    frozenset({"亥", "卯", "未"}),
    frozenset({"寅", "午", "戌"}),
    frozenset({"巳", "酉", "丑"}),
]

_BRANCH_KO_TO_ZH = {
    "자": "子", "축": "丑", "인": "寅", "묘": "卯", "진": "辰", "사": "巳",
    "오": "午", "미": "未", "신": "申", "유": "酉", "술": "戌", "해": "亥",
}

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
    named = [
        ("wood", element_profile.wood), ("fire", element_profile.fire),
        ("earth", element_profile.earth), ("metal", element_profile.metal),
        ("water", element_profile.water),
    ]
    name, count = max(named, key=lambda x: x[1])
    return name if count > 0 else None


_RAW_MIN = 30
_RAW_MAX = 75


def calculate(user_a: User, user_b: User) -> CompatibilityScore:
    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)

    score = 50

    a_day = saju_a.pillars[2]
    b_day = saju_b.pillars[2]
    a_stem_el = _STEM_ELEMENT.get(a_day.stem)
    b_stem_el = _STEM_ELEMENT.get(b_day.stem)

    if a_stem_el and b_stem_el:
        if a_stem_el == b_stem_el:
            score += 5
        elif _produces(a_stem_el, b_stem_el) or _produces(b_stem_el, a_stem_el):
            score += 10
        elif _controls(a_stem_el, b_stem_el) or _controls(b_stem_el, a_stem_el):
            score -= 10

    a_branch_zh = _BRANCH_KO_TO_ZH.get(a_day.branch)
    b_branch_zh = _BRANCH_KO_TO_ZH.get(b_day.branch)
    if a_branch_zh and b_branch_zh and a_branch_zh != b_branch_zh:
        pair = frozenset({a_branch_zh, b_branch_zh})
        if any(pair <= trine for trine in _TRINES):
            score += 10
        elif pair in _CLASH_PAIRS:
            score -= 10

    a_dom = _dominant_element(saju_a.element_profile)
    b_dom = _dominant_element(saju_b.element_profile)
    if a_dom and b_dom and (_produces(a_dom, b_dom) or _produces(b_dom, a_dom)):
        score += 5

    scaled = round((score - _RAW_MIN) / (_RAW_MAX - _RAW_MIN) * 100)
    score = max(0, min(100, scaled))

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


def _name_or_default(user: User) -> str:
    return user.nickname or f"사용자 {user.id}"


def _branch_relation(a_branch_ko: Optional[str], b_branch_ko: Optional[str]) -> str:
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


_ELEMENT_CHAT_TIP: dict[str, str] = {
    "fire": "불(火)의 기운이 강한 인연이니 밀당은 금물! 답장은 쿨하고 솔직하게 보낼 때 대화가 가장 불타오릅니다.",
    "water": "물(水)의 기운이 깊은 상대라, 가벼운 농담보다 진솔한 속마음을 한 줄 더 건넬 때 마음이 열려요.",
    "wood": "나무(木)의 기운이 자라는 상대라, 함께 그려갈 계획·미래 이야기를 꺼내면 대화가 쭉쭉 이어집니다.",
    "metal": "쇠(金)의 기운이 또렷한 상대라, 돌려 말하기보다 분명하고 깔끔한 표현이 호감을 키워요.",
    "earth": "흙(土)의 기운이 단단한 상대라, 급하게 몰아붙이기보다 꾸준한 안부가 신뢰를 쌓아줍니다.",
}


def _build_tip_line(name_b: str, b_dom: Optional[str]) -> str:
    if b_dom and b_dom in _ELEMENT_CHAT_TIP:
        return _ELEMENT_CHAT_TIP[b_dom]
    return (
        f"{name_b}님에게 먼저 가볍게 안부를 건네며 솔직한 관심을 표현해보세요. "
        "선톡이 대화의 물꼬를 터줍니다."
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
    tip = _build_tip_line(name_b, b_dom)

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
        summary_lines=ai["summary_lines"] if ai else [synergy, caution, tip],
        keywords=ai["keywords"] if ai else keywords,
    )


def _compute_age(birth_date: Optional[date]) -> Optional[int]:
    if birth_date is None:
        return None
    today = date.today()
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


async def _is_primary_face_verified(user: User, db: AsyncSession) -> bool:
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


async def _candidate_photos(user: User, db: AsyncSession) -> list[str]:
    from app.models.photo import UserPhoto

    rows = (
        await db.execute(
            select(UserPhoto)
            .where(UserPhoto.user_id == user.id)
            .order_by(UserPhoto.is_primary.desc(), UserPhoto.position.asc())
        )
    ).scalars().all()
    return [p.url for p in rows if p.url]


_KST = timezone(timedelta(hours=9))


def _snap_to_midnight_kst(dt: datetime) -> datetime:
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
            card.bio = candidate.bio
        except Exception:
            pass
    return card


async def _candidate_pool(
    current_user: User, db: AsyncSession,
) -> list[User]:
    blocked_by_me = select(UserBlock.blocked_id).where(
        UserBlock.blocker_id == current_user.id
    )
    blocked_me = select(UserBlock.blocker_id).where(
        UserBlock.blocked_id == current_user.id
    )
    stmt = (
        select(User)
        .where(User.id != current_user.id)
        .where(User.birth_date.is_not(None))
        .where(User.photo_url.is_not(None))
        .where(User.id.not_in(blocked_by_me))
        .where(User.id.not_in(blocked_me))
    )
    if current_user.gender == "male":
        stmt = stmt.where(User.gender == "female")
    elif current_user.gender == "female":
        stmt = stmt.where(User.gender == "male")
    return list((await db.execute(stmt)).scalars().all())