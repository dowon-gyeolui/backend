"""오늘의 인연운 — 사주 + 오늘 일진(日辰) 기반 일일 fortune (풍부한 버전).

Phase A: rule-based 깊이 강화. 입력으로 활용:
  - 사용자 일간(日干) — 천간 ten-god 관계
  - 사용자 일지(日支) — 충/합/도화 발동
  - 사용자 오행 분포 — 용신/기신
  - 오늘 일주(日柱) — 매일 KST 자정에 갱신
  - 오늘 천을귀인 / 도화 발동 여부

말투: 친근한 반말 (가벼운 친구가 사주 봐주는 느낌).
LLM 호출 없음 — rule-based 다층 조합 + 결정론적 템플릿 선택.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.user import User
from app.services.saju import calculate as calculate_saju
from app.services.saju_chart import (
    BRANCH_INFO,
    STEM_INFO,
    branch_ten_god,
    ten_god,
)
from app.services.saju_engine import _day_pillar
from app.services.saju_enrichment import (
    branch_element,
    branch_relation,
    color_for,
    estimate_yongsin_kisin,
    is_cheoneul_day,
    is_dohwa_day,
    place_for,
    stem_element,
    time_band_for,
)

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

_ELEMENT_KO = {
    "wood": "목", "fire": "화", "earth": "토",
    "metal": "금", "water": "수",
}


@dataclass
class TodayFortune:
    """API 응답 — 오늘의 인연운 (multi-section)."""

    fortune_text: str             # 메인 한국어 문구 (반말)
    today_pillar: str             # 오늘의 일주 (예: "갑술")
    today_pillar_hanja: str       # 한자 (예: "甲戌")
    relation: str                 # 사용자 ↔ 오늘의 십성
    element_today: str            # 오늘 일간의 오행 한국어
    score: int                    # 1~5 별점
    # 세부 섹션 — 프론트가 펼쳐 보여줄 수 있게 분리.
    headline: str = ""            # 한 줄 요약 (반말)
    person_type: str = ""         # 만나는 사람 성향
    timing: str = ""              # 좋은 시간대
    place: str = ""               # 좋은 장소 분위기
    caution: str = ""             # 주의사항
    lucky_color: str = ""         # 행운 색상
    badges: list[str] = field(default_factory=list)  # ['도화 발동', '천을귀인'] 같은 강조 칩


# --- 십성별 메인 헤드라인 (반말) -----------------------------------------

_HEADLINE_BY_RELATION: dict[str, list[str]] = {
    "비견": [
        "오늘은 친구·동료가 인연을 데려올 수 있어",
        "익숙한 자리에서 의외의 한 명이 보일지도",
    ],
    "겁재": [
        "오늘은 적극적으로 나서면 좋은 결과가 따라와",
        "한 발 먼저 다가가는 게 정답인 날",
    ],
    "식신": [
        "오늘은 표현력이 빛나는 하루야",
        "웃음 많은 자리에 가면 인연이 자연스럽게 돼",
    ],
    "상관": [
        "재치 있는 대화 한마디가 호감을 끌어내는 날",
        "솔직한 표현이 매력으로 다가가는 하루",
    ],
    "정재": [
        "이성과의 만남에 운이 따르는 좋은 날이야",
        "차분하게 다가가면 안정적인 관계로 발전할 수 있어",
    ],
    "편재": [
        "활발한 사교가 새로운 인연을 끌어와",
        "오늘은 직감을 믿고 끌리는 자리에 가봐",
    ],
    "정관": [
        "진중하고 신뢰감 있는 인연이 다가올 수 있는 날",
        "오랫동안 함께할 인연을 만날 수 있는 시기야",
    ],
    "편관": [
        "강한 인상이 통하는 하루 — 자신감 있게 행동해",
        "도전적인 만남이 새로운 가능성을 열어줘",
    ],
    "정인": [
        "따뜻한 마음이 좋은 인연을 부르는 날이야",
        "차분한 만남이 깊이 있는 인연으로 이어질 수 있어",
    ],
    "편인": [
        "직관이 예민해지는 날 — 끌리는 사람을 믿어봐",
        "독특한 매력이 빛나는 하루야",
    ],
    "—": [
        "평소처럼 자연스럽게 임하면 되는 무난한 날이야",
        "오늘은 마음 가는 대로 흘러가도 좋아",
    ],
}

# 십성별 만나는 사람 성향 (반말)
_PERSON_TYPE_BY_RELATION: dict[str, str] = {
    "비견": "비슷한 결을 가진 친근한 사람",
    "겁재": "활발하고 추진력 있는 사람",
    "식신": "여유롭고 즐거운 사람",
    "상관": "재치 있고 표현력 좋은 사람",
    "정재": "차분하고 안정감 있는 이성",
    "편재": "활동적이고 사교적인 이성",
    "정관": "진중하고 신뢰감 있는 사람",
    "편관": "카리스마 있고 강단 있는 사람",
    "정인": "따뜻하고 배려심 깊은 사람",
    "편인": "독특한 개성을 가진 사람",
    "—": "마음 편한 사람",
}

# 십성별 인연 운 강도
_RELATION_SCORE: dict[str, int] = {
    "정재": 5, "편재": 5,
    "정관": 5, "편관": 4,
    "식신": 4, "상관": 4,
    "정인": 3, "편인": 3,
    "비견": 3, "겁재": 3,
    "—": 3,
}

# 충/합 보정 (점수 + 메시지)
def _adjust_for_branch_relation(rel: str) -> tuple[int, Optional[str], Optional[str]]:
    """(score_delta, badge, caution_text)"""
    if rel == "삼합":
        return +1, "삼합 길일", None
    if rel == "합":
        return +1, "육합 길일", None
    if rel == "충":
        return -1, "육충 주의", "오늘은 너무 직설적인 표현은 피하는 게 좋아"
    return 0, None, None


def today_day_pillar_kst() -> tuple[str, str]:
    today = datetime.now(_KST).date()
    return _day_pillar(today)


def _hanja_pillar(stem: str, branch: str) -> str:
    s = STEM_INFO.get(stem, {}).get("hanja", "")
    b = BRANCH_INFO.get(branch, {}).get("hanja", "")
    return f"{s}{b}"


def compute_today_fortune(user: User) -> Optional[TodayFortune]:
    """사용자 사주 + 오늘 일진 → 풍부한 인연운 객체.

    user.birth_date 가 없으면 None.
    """
    if user.birth_date is None:
        return None

    try:
        user_saju = calculate_saju(user)
    except Exception as e:
        logger.warning("calculate_saju failed for user %s: %s", user.id, e)
        return None

    user_day = user_saju.pillars[2]
    user_day_stem = user_day.stem
    user_day_branch = user_day.branch
    user_elements = user_saju.element_profile

    # 오늘 일주
    today_kst = datetime.now(_KST).date()
    today_stem, today_branch = _day_pillar(today_kst)

    # 십성 관계 (천간 우선, 실패 시 지지 본기)
    relation = ten_god(user_day_stem, today_stem)
    if relation == "—":
        relation = branch_ten_god(user_day_stem, today_branch)

    base_score = _RELATION_SCORE.get(relation, 3)
    badges: list[str] = []
    cautions: list[str] = []

    # 일지 충/합/삼합 보정
    branch_rel = branch_relation(user_day_branch, today_branch)
    delta, badge, caution = _adjust_for_branch_relation(branch_rel)
    base_score += delta
    if badge:
        badges.append(badge)
    if caution:
        cautions.append(caution)

    # 도화 발동
    if is_dohwa_day(user_day_branch, today_branch):
        badges.append("도화 발동")
        base_score += 1

    # 천을귀인
    if is_cheoneul_day(user_day_stem, today_branch):
        badges.append("천을귀인 길일")
        base_score += 1

    score = max(1, min(5, base_score))

    # 닉네임
    nickname = (user.nickname or "").strip() or "OOO"

    # seed 기반 템플릿 선택
    seed = today_kst.toordinal() ^ ((user.id or 0) * 2654435761) & 0xFFFFFFFF

    headline_pool = _HEADLINE_BY_RELATION.get(
        relation, _HEADLINE_BY_RELATION["—"]
    )
    headline = headline_pool[seed % len(headline_pool)]

    person_type = _PERSON_TYPE_BY_RELATION.get(relation, "마음 편한 사람")

    # 시간대 / 장소 추천 — 사용자 용신 우선, 없으면 오늘 일간 오행
    counts = {
        "wood": user_elements.wood, "fire": user_elements.fire,
        "earth": user_elements.earth, "metal": user_elements.metal,
        "water": user_elements.water,
    }
    yongsin, _kisin = estimate_yongsin_kisin(counts)
    today_el = stem_element(today_stem)
    target_el = yongsin or today_el  # 추천 대상 오행

    timing = time_band_for(target_el) if target_el else "오후"
    place = place_for(target_el) if target_el else "조용한 카페"
    lucky_color = color_for(target_el) if target_el else "흰색"

    # caution 문구 종합 (없으면 일반)
    if cautions:
        caution_text = " · ".join(cautions)
    else:
        if relation in ("편관", "겁재"):
            caution_text = "강하게 밀어붙이지는 말고 적당한 거리감 유지해"
        elif relation in ("상관",):
            caution_text = "솔직함은 좋은데 너무 직설적이지는 않게"
        else:
            caution_text = "평소대로 자연스럽게 행동하면 충분해"

    # 메인 fortune_text — 헤드라인을 닉네임 포함해 자연스럽게
    fortune_text = (
        f'"{nickname}아, {headline}.\n오늘은 {person_type}을 만나기 좋은 날이야."'
        if not nickname.endswith(("님",))
        else f'"{nickname}, {headline}.\n오늘은 {person_type}을 만나기 좋은 날이야."'
    )

    today_element_en = stem_element(today_stem)
    element_today_ko = (
        _ELEMENT_KO.get(today_element_en, "") if today_element_en else ""
    )

    return TodayFortune(
        fortune_text=fortune_text,
        today_pillar=f"{today_stem}{today_branch}",
        today_pillar_hanja=_hanja_pillar(today_stem, today_branch),
        relation=relation,
        element_today=element_today_ko,
        score=score,
        headline=headline,
        person_type=person_type,
        timing=timing,
        place=place,
        caution=caution_text,
        lucky_color=lucky_color,
        badges=badges,
    )