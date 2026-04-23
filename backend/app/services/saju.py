"""Saju calculation service — placeholder implementation.

All pillar derivations use simple modular arithmetic on the birth date.
They produce output that varies with input and follows the correct structure,
but are NOT accurate traditional calculations.

Replacement points are marked with TODO comments:
  - _year_pillar:  TODO certified 60-cycle engine
  - _month_pillar: TODO 절기 (solar term) based month pillar
  - _day_pillar:   TODO exact day pillar lookup table
  - _time_pillar:  TODO 五鼠遁日法 for correct time stem
  - _element_profile: TODO include earthly branch elements (지지 오행)
"""

from datetime import date as DateType
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.saju import (
    BirthInputSummary,
    ElementProfile,
    Pillar,
    SajuResponse,
)

# Five-element Korean name (used when building retrieval queries)
_ELEMENT_KO = {
    "wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수",
}

# 10 heavenly stems (천간)
_STEMS = ["갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"]

# 12 earthly branches (지지)
_BRANCHES = ["자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"]

# Five-element mapping for each heavenly stem
_STEM_ELEMENT: dict[str, str] = {
    "갑": "wood", "을": "wood",
    "병": "fire", "정": "fire",
    "무": "earth", "기": "earth",
    "경": "metal", "신": "metal",
    "임": "water", "계": "water",
}

_UNKNOWN = "미상"

# Reference date for day pillar offset (approximate 갑자일)
_DAY_REFERENCE = DateType(1900, 1, 1)
_DAY_REFERENCE_OFFSET = 6  # rough alignment offset


def _year_pillar(year: int) -> Pillar:
    # TODO: Replace with certified 60-cycle 사주 engine
    stem = _STEMS[(year - 4) % 10]
    branch = _BRANCHES[(year - 4) % 12]
    return Pillar(label="년주", stem=stem, branch=branch, combined=stem + branch)


def _month_pillar(year: int, month: int) -> Pillar:
    # TODO: Replace with 절기 (solar term) based month pillar calculation
    # Simplified: month branch shifts by month, stem derived from year stem group
    branch = _BRANCHES[(month + 1) % 12]
    year_stem_idx = (year - 4) % 10
    stem = _STEMS[(year_stem_idx % 5 * 2 + (month - 1)) % 10]
    return Pillar(label="월주", stem=stem, branch=branch, combined=stem + branch)


def _day_pillar(birth_date: DateType) -> Pillar:
    # TODO: Replace with authoritative day pillar lookup or calculation table
    days = (birth_date - _DAY_REFERENCE).days
    stem = _STEMS[(days + _DAY_REFERENCE_OFFSET) % 10]
    branch = _BRANCHES[(days + _DAY_REFERENCE_OFFSET) % 12]
    return Pillar(label="일주", stem=stem, branch=branch, combined=stem + branch)


def _time_pillar(birth_time: Optional[str], day_stem: str) -> Pillar:
    if birth_time is None:
        return Pillar(
            label="시주",
            stem=_UNKNOWN,
            branch=_UNKNOWN,
            combined=_UNKNOWN,
        )
    hour = int(birth_time.split(":")[0])
    # Each 지지 covers a two-hour window; 자시 starts at 23:00
    branch_index = (hour + 1) // 2 % 12
    branch = _BRANCHES[branch_index]
    # TODO: Apply 五鼠遁日法 for correct time stem derivation
    day_stem_idx = _STEMS.index(day_stem) if day_stem in _STEMS else 0
    stem = _STEMS[(day_stem_idx % 5 * 2 + branch_index // 2) % 10]
    return Pillar(label="시주", stem=stem, branch=branch, combined=stem + branch)


def _element_profile(pillars: list[Pillar]) -> ElementProfile:
    # TODO: Include earthly branch (지지) elements for a full 8-character analysis
    counts: dict[str, int] = {
        "wood": 0, "fire": 0, "earth": 0, "metal": 0, "water": 0
    }
    for pillar in pillars:
        element = _STEM_ELEMENT.get(pillar.stem)
        if element:
            counts[element] += 1
    return ElementProfile(**counts)


def _korean_summary(ep: ElementProfile) -> str:
    named = [
        ("목(木)", ep.wood),
        ("화(火)", ep.fire),
        ("토(土)", ep.earth),
        ("금(金)", ep.metal),
        ("수(水)", ep.water),
    ]
    dominant_name, dominant_count = max(named, key=lambda x: x[1])
    distribution = " · ".join(f"{name} {cnt}" for name, cnt in named)
    if dominant_count == 0:
        dominant_line = "출생 시간 미입력으로 오행 분포를 완전히 산출하지 못했습니다."
    else:
        dominant_line = f"주요 오행은 {dominant_name}입니다."
    return (
        f"[임시 분석] 오행 분포: {distribution}. "
        f"{dominant_line} "
        f"정확한 해석은 원전 기반 분석 연동 후 제공될 예정입니다."
    )


def calculate(user: User) -> SajuResponse:
    """Derive a placeholder saju result from the user's stored birth data.

    Raises ValueError if birth_date is not set (caller should return HTTP 400).
    """
    if user.birth_date is None:
        raise ValueError("birth_date is required for saju calculation")

    year_p = _year_pillar(user.birth_date.year)
    month_p = _month_pillar(user.birth_date.year, user.birth_date.month)
    day_p = _day_pillar(user.birth_date)
    time_p = _time_pillar(user.birth_time, day_p.stem)

    pillars = [year_p, month_p, day_p, time_p]
    ep = _element_profile(pillars)

    return SajuResponse(
        user_id=user.id,
        input_summary=BirthInputSummary(
            birth_date=user.birth_date,
            birth_time=user.birth_time,
            calendar_type=user.calendar_type or "solar",
            is_leap_month=user.is_leap_month,
            gender=user.gender,
        ),
        pillars=pillars,
        element_profile=ep,
        summary=_korean_summary(ep),
        interpretation_status="pending",
        interpretation_sources=[],
    )


# --- Retrieval-grounded enrichment -----------------------------------

def _build_retrieval_queries(saju: SajuResponse) -> list[str]:
    """Extract key signals from the saju result into retrieval queries."""
    queries: list[str] = []

    ep = saju.element_profile
    named = [
        ("목", ep.wood), ("화", ep.fire), ("토", ep.earth),
        ("금", ep.metal), ("수", ep.water),
    ]
    dominant_name, dominant_count = max(named, key=lambda x: x[1])
    if dominant_count > 0:
        queries.append(f"{dominant_name} 오행 성질 특징")

    # Day pillar — index 2 is 일주
    day_pillar = saju.pillars[2]
    if day_pillar.stem in _STEMS:
        element_key = _STEM_ELEMENT[day_pillar.stem]
        ko_element = _ELEMENT_KO[element_key]
        queries.append(f"{day_pillar.stem}{ko_element} 일주 성질 격국")

    return queries


async def enrich_with_interpretation(
    saju: SajuResponse,
    db: AsyncSession,
) -> SajuResponse:
    """Attach retrieval-grounded source citations to a saju response.

    Only vector_similarity matches count — keyword or placeholder results
    do NOT flip interpretation_status to "ready".
    Retrieval or embedding failures are swallowed; the base saju result is
    returned with status still "pending".
    """
    # Imported here to avoid a circular import at module load time.
    from app.schemas.knowledge import KnowledgeQuery
    from app.services.knowledge.retrieval import retrieve

    queries = _build_retrieval_queries(saju)
    citations: list[str] = []

    for q in queries:
        try:
            results = await retrieve(KnowledgeQuery(query=q, top_k=2), db)
        except Exception:
            continue
        for r in results:
            if r.match_reason == "vector_similarity" and r.chunk.id != 0:
                citations.append(r.source_citation)

    seen: set[str] = set()
    unique: list[str] = []
    for c in citations:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if unique:
        saju.interpretation_sources = unique
        saju.interpretation_status = "ready"

    return saju
