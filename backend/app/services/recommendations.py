"""Recommendation service.

Two modes matching the product flow:

  1. Pre-match (free tier):
       GET /recommendations/me
       Rule-based color/place/styling tips derived from the user's saju
       dominant element. No LLM, no RAG — deterministic and always available.

  2. Post-match (paid tier):
       GET /recommendations/pair/{target_user_id}
       Pair-level tips for two users who already saw each other's card.
       Uses compatibility score + element relationship to build retrieval
       queries, pulls classical passages via vector search, then asks an LLM
       to produce strengths / cautions / conversation_starters grounded in
       those passages. Falls back gracefully when LLM is unavailable.

The pre-match path is always safe. The post-match path degrades to empty
lists + no summary when retrieval or the LLM fails — the caller still gets
a valid response.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.recommendation import PairRecommendation, RecommendationCard
from app.services.compatibility import calculate as calculate_compatibility
from app.services.saju import (
    _ELEMENT_KO,
    _STEM_ELEMENT,
    calculate as calculate_saju,
)

# Deterministic per-element mappings (pre-match). Conservative and on-brand.
_ELEMENT_RECOMMENDATIONS: dict[str, dict] = {
    "wood": {
        "colors": ["초록", "연두", "갈색"],
        "places": ["공원", "숲길", "식물원", "북카페"],
        "styling": "편안한 린넨·면 소재, 자연스러운 질감의 의상이 잘 어울립니다.",
    },
    "fire": {
        "colors": ["붉은색", "주황", "코랄"],
        "places": ["루프탑 바", "야경 명소", "공연장", "댄스 클래스"],
        "styling": "활기를 살리는 선명한 포인트 컬러, 또렷한 실루엣을 추천합니다.",
    },
    "earth": {
        "colors": ["노랑", "베이지", "카키"],
        "places": ["전시회", "공방", "브런치 카페", "근교 드라이브"],
        "styling": "안정감 있는 톤의 베이직 코디, 소재 밀도가 단단한 아이템이 좋습니다.",
    },
    "metal": {
        "colors": ["화이트", "실버", "그레이"],
        "places": ["미술관", "재즈 바", "호텔 라운지", "서점"],
        "styling": "깔끔한 실루엣의 미니멀 코디, 단정한 액세서리가 어울립니다.",
    },
    "water": {
        "colors": ["네이비", "블루", "블랙"],
        "places": ["바닷가", "수족관", "조용한 호수", "북 라운지"],
        "styling": "차분한 톤의 유려한 실루엣, 실크/니트처럼 부드러운 소재가 좋습니다.",
    },
}


def _dominant_element_key(element_profile) -> Optional[str]:
    """English element key with the highest count, or None when all zero."""
    named = [
        ("wood", element_profile.wood), ("fire", element_profile.fire),
        ("earth", element_profile.earth), ("metal", element_profile.metal),
        ("water", element_profile.water),
    ]
    name, count = max(named, key=lambda x: x[1])
    return name if count > 0 else None


def recommend_pre_match(user: User) -> RecommendationCard:
    """Pre-match recommendation for a single user. Free-tier safe."""
    if user.birth_date is None:
        return RecommendationCard(
            user_id=user.id,
            summary="생년월일을 먼저 입력하시면 맞춤 추천을 드릴 수 있습니다.",
        )

    saju = calculate_saju(user)
    dom_key = _dominant_element_key(saju.element_profile)
    dom_ko = _ELEMENT_KO.get(dom_key) if dom_key else None

    if dom_key is None:
        return RecommendationCard(
            user_id=user.id,
            summary="출생 시간이 입력되지 않아 주요 오행을 산출하지 못했습니다. 시간을 입력하시면 더 정확한 추천을 제공합니다.",
        )

    preset = _ELEMENT_RECOMMENDATIONS[dom_key]
    summary = (
        f"당신의 주요 오행은 {dom_ko}(五行) 입니다. "
        f"{', '.join(preset['colors'][:2])} 계열 옷차림과 "
        f"{', '.join(preset['places'][:2])} 같은 장소가 자신의 기운을 돋우고 "
        f"좋은 인연을 만날 확률을 높이는 방향입니다."
    )
    return RecommendationCard(
        user_id=user.id,
        dominant_element=dom_ko,
        colors=preset["colors"],
        places=preset["places"],
        styling=preset["styling"],
        summary=summary,
    )


# --- Post-match pair recommendations (paid tier) ---------------------

async def recommend_pair(
    user_a: User,
    user_b: User,
    db: AsyncSession,
) -> PairRecommendation:
    """Post-match pair recommendation — RAG + LLM grounded in classical texts."""
    # Local imports to avoid module-load cycles.
    from app.schemas.knowledge import KnowledgeQuery
    from app.services.knowledge.retrieval import retrieve
    from app.services.llm.interpret import (
        RetrievedPassage,
        generate_pair_recommendation,
    )

    if user_a.birth_date is None or user_b.birth_date is None:
        # Caller should guard this, but fail gracefully just in case.
        return PairRecommendation(
            user_a_id=user_a.id,
            user_b_id=user_b.id,
            compatibility_score=0,
        )

    cs = calculate_compatibility(user_a, user_b)
    saju_a = calculate_saju(user_a)
    saju_b = calculate_saju(user_b)

    dom_a = _dominant_element_key(saju_a.element_profile)
    dom_b = _dominant_element_key(saju_b.element_profile)
    day_a = saju_a.pillars[2]
    day_b = saju_b.pillars[2]

    # Retrieval queries — one element-pair, one day-pair.
    queries: list[str] = []
    if dom_a and dom_b:
        queries.append(
            f"{_ELEMENT_KO[dom_a]} {_ELEMENT_KO[dom_b]} 오행 궁합 강점 약점"
        )
    if day_a.stem in _STEM_ELEMENT and day_b.stem in _STEM_ELEMENT:
        queries.append(
            f"{day_a.combined} {day_b.combined} 일주 궁합 대화"
        )

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
            if r.source_citation in passages_by_citation:
                continue
            passages_by_citation[r.source_citation] = RetrievedPassage(
                citation=r.source_citation,
                content=r.chunk.content or "",
            )
            citations.append(r.source_citation)

    strengths: list[str] = []
    cautions: list[str] = []
    starters: list[str] = []
    summary: Optional[str] = None

    if passages_by_citation:
        ordered = [passages_by_citation[c] for c in citations]
        llm_out = generate_pair_recommendation(
            score=cs.score,
            user_a_info={
                "nickname": user_a.nickname,
                "day_pillar": day_a.combined,
                "dominant_element": _ELEMENT_KO.get(dom_a) if dom_a else None,
                "gender": user_a.gender,
            },
            user_b_info={
                "nickname": user_b.nickname,
                "day_pillar": day_b.combined,
                "dominant_element": _ELEMENT_KO.get(dom_b) if dom_b else None,
                "gender": user_b.gender,
            },
            passages=ordered,
        )
        if llm_out is not None:
            strengths = llm_out.get("strengths", [])[:3]
            cautions = llm_out.get("cautions", [])[:3]
            starters = llm_out.get("conversation_starters", [])[:3]
            summary = llm_out.get("summary")

    return PairRecommendation(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        compatibility_score=cs.score,
        strengths=strengths,
        cautions=cautions,
        conversation_starters=starters,
        summary=summary,
        sources=citations,
    )
