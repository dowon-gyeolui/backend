"""사주(사주팔자) 계산 및 원전 RAG 기반 해석·자미두수 풀이 서비스."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.saju import (
    BirthInputSummary,
    ElementProfile,
    Pillar,
    SajuResponse,
)

_ELEMENT_KO = {
    "wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수",
}

_STEMS = ["갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"]

_BRANCHES = ["자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"]

_STEM_ELEMENT: dict[str, str] = {
    "갑": "wood", "을": "wood",
    "병": "fire", "정": "fire",
    "무": "earth", "기": "earth",
    "경": "metal", "신": "metal",
    "임": "water", "계": "water",
}

_UNKNOWN = "미상"


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
            return Pillar(label=label, stem=_UNKNOWN, branch=_UNKNOWN, combined=_UNKNOWN)
        stem, branch = p
        return Pillar(label=label, stem=stem, branch=branch, combined=stem + branch)

    pillars = [
        to_pillar("년주", fp.year),
        to_pillar("월주", fp.month),
        to_pillar("일주", fp.day),
        to_pillar("시주", fp.time),
    ]

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


def _build_retrieval_queries(saju: SajuResponse) -> list[str]:
    queries: list[str] = []

    ep = saju.element_profile
    named = [
        ("목", ep.wood), ("화", ep.fire), ("토", ep.earth),
        ("금", ep.metal), ("수", ep.water),
    ]
    dominant_name, dominant_count = max(named, key=lambda x: x[1])
    if dominant_count > 0:
        queries.append(f"{dominant_name} 오행 성질 특징")

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

    if not unique_citations:
        return saju

    saju.interpretation_sources = unique_citations
    saju.interpretation_status = "ready"

    ordered_passages = [passages_by_citation[c] for c in unique_citations]
    saju.interpretation = await asyncio.to_thread(
        generate_saju_interpretation, saju, ordered_passages
    )

    return saju


async def enrich_with_detailed_interpretation(
    saju: "SajuResponse",
    db: "AsyncSession",
):
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
        return base

    base.interpretation_sources = unique_citations
    base.interpretation_status = "ready"

    ordered_passages = [passages_by_citation[c] for c in unique_citations]
    sections = await asyncio.to_thread(
        generate_detailed_interpretation, saju, ordered_passages
    )
    if sections:
        base.personality = sections.get("personality", "")
        base.love = sections.get("love", "")
        base.wealth = sections.get("wealth", "")
        base.advice = sections.get("advice", "")

    return base


async def build_jamidusu_for(user: User) -> "JamidusuResponse":
    from app.schemas.saju import JamidusuPalace, JamidusuResponse
    from app.services.llm.interpret import generate_jamidusu_interpretation

    saju = calculate(user)
    result = JamidusuResponse(user_id=user.id)

    sections = await asyncio.to_thread(generate_jamidusu_interpretation, saju)
    if not sections:
        return result

    result.overview = sections.get("overview", "")
    result.main_stars_summary = sections.get("main_stars_summary", "")
    result.palaces = [
        JamidusuPalace(name=p["name"], description=p["description"])
        for p in sections.get("palaces", [])
    ]
    if result.overview or result.palaces or result.main_stars_summary:
        result.interpretation_status = "ready"
    return result


def _chart_to_dict(chart: "JamidusuChart") -> dict:
    return {
        "lunar_year": chart.lunar_year,
        "lunar_month": chart.lunar_month,
        "lunar_day": chart.lunar_day,
        "is_leap_month": chart.is_leap_month,
        "hour_assumed": chart.hour_assumed,
        "year_pillar": chart.year_pillar,
        "bureau_name": chart.bureau_name,
        "bureau_num": chart.bureau_num,
        "ming_branch_idx": chart.ming_branch_idx,
        "ziwei_branch_idx": chart.ziwei_branch_idx,
        "palaces": [
            {
                "name": p.name,
                "name_ko": p.name_ko,
                "branch": p.branch,
                "branch_ko": p.branch_ko,
                "stem": p.stem,
                "stem_ko": p.stem_ko,
                "stars": [
                    {
                        "name": s.name,
                        "name_ko": s.name_ko,
                        "type": s.type,
                        "sub": s.sub,
                    }
                    for s in p.stars
                ],
            }
            for p in chart.palaces
        ],
    }


def _build_jamidusu_retrieval_queries(saju: SajuResponse) -> list[str]:
    day_pillar = saju.pillars[2]
    queries = [
        f"{day_pillar.combined} 일주 자미두수",
        f"{day_pillar.stem} 일간 명궁",
        "자미 천부 명궁 부처궁",
        f"{day_pillar.combined} 일주 성격",
    ]
    return queries


async def build_jamidusu_deep_for(
    user: "User",
    db: "AsyncSession",
):
    from app.schemas.knowledge import KnowledgeQuery
    from app.schemas.saju import (
        JamidusuDeepPalace,
        JamidusuDeepResponse,
        JamidusuDeepStar,
    )
    from app.services.jamidusu import compute_chart
    from app.services.knowledge.retrieval import retrieve
    from app.services.llm.interpret import (
        RetrievedPassage,
        generate_jamidusu_deep,
    )

    if user.birth_date is None:
        return JamidusuDeepResponse(user_id=user.id, interpretation_status="pending")

    try:
        chart = compute_chart(
            user.birth_date,
            birth_time=user.birth_time,
            calendar_type=user.calendar_type or "solar",
            is_leap_month=bool(user.is_leap_month),
            gender=user.gender,
        )
    except Exception:
        return JamidusuDeepResponse(user_id=user.id, interpretation_status="pending")

    chart_dict = _chart_to_dict(chart)

    saju = calculate(user)

    queries = _build_jamidusu_retrieval_queries(saju)
    passages_by_citation: dict[str, RetrievedPassage] = {}
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
    ordered_passages = [passages_by_citation[c] for c in unique_citations]

    palaces_response = [
        JamidusuDeepPalace(
            name=p.name,
            name_ko=p.name_ko,
            branch=p.branch,
            branch_ko=p.branch_ko,
            stem=p.stem,
            stem_ko=p.stem_ko,
            stars=[
                JamidusuDeepStar(
                    name=s.name, name_ko=s.name_ko, type=s.type, sub=s.sub
                )
                for s in p.stars
            ],
            description="",
        )
        for p in chart.palaces
    ]

    response = JamidusuDeepResponse(
        user_id=user.id,
        interpretation_status="pending",
        bureau_name=chart.bureau_name,
        year_pillar=chart.year_pillar,
        lunar_birth=(
            f"{chart.lunar_year}-{chart.lunar_month:02d}-{chart.lunar_day:02d}"
            + (" (윤달)" if chart.is_leap_month else "")
        ),
        hour_assumed=chart.hour_assumed,
        palaces=palaces_response,
        sources=unique_citations,
    )

    llm_result = await asyncio.to_thread(
        generate_jamidusu_deep, saju, chart_dict, ordered_passages
    )
    if llm_result is None:
        response.interpretation_status = "partial"
        return response

    palace_data_by_ko: dict[str, dict] = {
        p["name_ko"]: p
        for p in (llm_result.get("palaces") or [])
        if isinstance(p, dict) and p.get("name_ko")
    }
    for p in response.palaces:
        pd = palace_data_by_ko.get(p.name_ko, {})
        p.app_title = pd.get("app_title", "")
        p.summary = pd.get("summary", "")
        p.love_interpretation = pd.get("love_interpretation", "")
        p.love_tip = pd.get("love_tip", "")
        p.keywords = [str(k) for k in (pd.get("keywords") or []) if k]

    response.interpretation_status = "ready"
    return response
