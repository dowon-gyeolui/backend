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
    """Real 60-cycle saju calculation backed by ``services.saju_engine``.

    Computes:
      - 년주: 입춘(立春) 기준
      - 월주: 12절(節)로 月支 + 五虎遁으로 月干
      - 일주: 1900-01-31 갑진일 기준점에서 일수 차이로 60갑자 순환
      - 시주: 12 시진 매핑 + 五鼠遁으로 時干
    Element distribution counts both 천간(stem) and 지지(branch) — 8자 분석.
    Raises ValueError if birth_date is not set (caller should return HTTP 400).
    """
    if user.birth_date is None:
        raise ValueError("birth_date is required for saju calculation")

    from app.services.saju_engine import (
        calculate_four_pillars,
        element_distribution_from_pillars,
    )

    calendar = user.calendar_type if user.calendar_type in ("solar", "lunar") else "solar"
    fp = calculate_four_pillars(
        user.birth_date,
        user.birth_time,
        calendar_type=calendar,  # type: ignore[arg-type]
        is_leap_month=user.is_leap_month,
        birth_place=user.birth_place,
    )

    def to_pillar(label: str, p: tuple[str, str] | None) -> Pillar:
        if p is None:
            # 시주 미상
            return Pillar(label=label, stem=_UNKNOWN, branch=_UNKNOWN, combined=_UNKNOWN)
        stem, branch = p
        return Pillar(label=label, stem=stem, branch=branch, combined=stem + branch)

    pillars = [
        to_pillar("년주", fp.year),
        to_pillar("월주", fp.month),
        to_pillar("일주", fp.day),
        to_pillar("시주", fp.time),
    ]

    # Enrich each pillar with chart fields (십성·지장간·12운성·12신살).
    # 일간 = pillars[2].stem; 년지 = pillars[0].branch.
    from app.services.saju_chart import (
        BRANCH_INFO,
        HIDDEN_STEMS,
        STEM_INFO,
        branch_ten_god,
        ten_god,
        twelve_spirit,
        twelve_stage,
    )

    day_stem = pillars[2].stem
    year_branch = pillars[0].branch

    for i, p in enumerate(pillars):
        if p.stem in STEM_INFO:
            si = STEM_INFO[p.stem]
            p.stem_hanja = si["hanja"]
            p.stem_element = si["element"]
            p.stem_polarity = si["polarity"]
            # 일주의 일간 자체는 비견(자기 자신)이라 표시하지 않음.
            if day_stem in STEM_INFO and i != 2:
                p.stem_ten_god = ten_god(day_stem, p.stem)
        if p.branch in BRANCH_INFO:
            bi = BRANCH_INFO[p.branch]
            p.branch_hanja = bi["hanja"]
            p.branch_animal = bi["animal"]
            p.branch_element = bi["element"]
            p.branch_polarity = bi["polarity"]
            if day_stem in STEM_INFO:
                p.branch_ten_god = branch_ten_god(day_stem, p.branch)
                p.twelve_stage = twelve_stage(day_stem, p.branch)
            if year_branch in BRANCH_INFO:
                p.twelve_spirit = twelve_spirit(year_branch, p.branch)
            p.hidden_stems = list(HIDDEN_STEMS.get(p.branch, []))

    counts = element_distribution_from_pillars(fp)
    ep = ElementProfile(**counts)

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
    """Attach retrieval-grounded citations + LLM-generated interpretation.

    Flow:
      1. Build retrieval queries from dominant element + day pillar.
      2. Call vector search; collect unique citations + source passages.
      3. If at least one vector_similarity result exists → set status="ready".
      4. Pass passages to the LLM interpreter (best-effort, failures swallowed).

    Only vector_similarity matches count — keyword/placeholder results do NOT
    flip status to "ready".
    """
    # Local imports to avoid module-load-time cycles.
    from app.schemas.knowledge import KnowledgeQuery
    from app.services.knowledge.retrieval import retrieve
    from app.services.llm.interpret import (
        RetrievedPassage,
        generate_saju_interpretation,
    )

    queries = _build_retrieval_queries(saju)
    citations: list[str] = []
    passages_by_citation: dict[str, RetrievedPassage] = {}

    for q in queries:
        try:
            results = await retrieve(KnowledgeQuery(query=q, top_k=2), db)
        except Exception:
            continue
        for r in results:
            if r.match_reason != "vector_similarity" or r.chunk.id == 0:
                continue
            citations.append(r.source_citation)
            # Deduplicate by citation — one passage per source-chapter-section.
            if r.source_citation not in passages_by_citation:
                passages_by_citation[r.source_citation] = RetrievedPassage(
                    citation=r.source_citation,
                    content=r.chunk.content or "",
                )

    # Preserve first-seen order for citations.
    seen: set[str] = set()
    unique_citations: list[str] = []
    for c in citations:
        if c not in seen:
            seen.add(c)
            unique_citations.append(c)

    if not unique_citations:
        return saju  # status stays "pending"

    saju.interpretation_sources = unique_citations
    saju.interpretation_status = "ready"

    # LLM call is best-effort — if it fails, the UI still has the citations.
    ordered_passages = [passages_by_citation[c] for c in unique_citations]
    saju.interpretation = generate_saju_interpretation(saju, ordered_passages)

    return saju


async def enrich_with_detailed_interpretation(
    saju: "SajuResponse",
    db: "AsyncSession",
):
    """Like ``enrich_with_interpretation`` but produces a 5-section
    interpretation (성격/연애/재물/건강/조언). Returns DetailedSajuResponse.

    Falls back to empty section strings when the LLM fails or no passages
    match — the frontend renders graceful placeholders for empties.
    """
    from app.schemas.knowledge import KnowledgeQuery
    from app.schemas.saju import DetailedSajuResponse
    from app.services.knowledge.retrieval import retrieve
    from app.services.llm.interpret import (
        RetrievedPassage,
        generate_detailed_interpretation,
    )

    queries = _build_retrieval_queries(saju)
    passages_by_citation: dict[str, "RetrievedPassage"] = {}
    citations: list[str] = []

    for q in queries:
        try:
            results = await retrieve(KnowledgeQuery(query=q, top_k=3), db)
        except Exception:
            continue
        for r in results:
            if r.match_reason != "vector_similarity" or r.chunk.id == 0:
                continue
            citations.append(r.source_citation)
            if r.source_citation not in passages_by_citation:
                passages_by_citation[r.source_citation] = RetrievedPassage(
                    citation=r.source_citation,
                    content=r.chunk.content or "",
                )

    seen: set[str] = set()
    unique_citations: list[str] = []
    for c in citations:
        if c not in seen:
            seen.add(c)
            unique_citations.append(c)

    base = DetailedSajuResponse(
        **saju.model_dump(),
    )

    if not unique_citations:
        return base  # all sections stay empty, status pending

    base.interpretation_sources = unique_citations
    base.interpretation_status = "ready"

    ordered_passages = [passages_by_citation[c] for c in unique_citations]
    sections = generate_detailed_interpretation(saju, ordered_passages)
    if sections:
        base.personality = sections.get("personality", "")
        base.love = sections.get("love", "")
        base.wealth = sections.get("wealth", "")
        base.advice = sections.get("advice", "")

    return base


def build_jamidusu_for(user: User) -> "JamidusuResponse":
    """자미두수 12궁·14주성 LLM 풀이.

    Unlike the saju endpoints, 자미두수 doesn't use the RAG corpus —
    we don't have classical 자미두수 passages indexed yet. The LLM is
    seeded entirely from the user's saju, which is acceptable for the
    paid drawer until a proper 자미두수 calc engine + corpus lands.
    """
    from app.schemas.saju import JamidusuPalace, JamidusuResponse
    from app.services.llm.interpret import generate_jamidusu_interpretation

    saju = calculate(user)
    result = JamidusuResponse(user_id=user.id)

    sections = generate_jamidusu_interpretation(saju)
    if not sections:
        return result  # status stays "pending"

    result.overview = sections.get("overview", "")
    result.main_stars_summary = sections.get("main_stars_summary", "")
    result.palaces = [
        JamidusuPalace(name=p["name"], description=p["description"])
        for p in sections.get("palaces", [])
    ]
    if result.overview or result.palaces or result.main_stars_summary:
        result.interpretation_status = "ready"
    return result
