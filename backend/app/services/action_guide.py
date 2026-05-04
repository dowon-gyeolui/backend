"""오늘의 행동 가이드 — 사주 기반 동적 추천.

사용자의 일주 + 오행 분포 + 오늘 일진을 종합해 다음 항목을 추천:
  - 오늘의 컬러 (용신 오행 기반)
  - 추천 시간대
  - 추천 장소 분위기
  - 추천 의상 스타일
  - 추천 향수
  - 행운 숫자
  - 잘 맞는 띠

Phase A — rule-based, LLM 호출 없음. 반말 톤.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.user import User
from app.services.saju import calculate as calculate_saju
from app.services.saju_engine import _day_pillar
from app.services.saju_enrichment import (
    color_for,
    direction_for,
    estimate_yongsin_kisin,
    fashion_for,
    food_for,
    lucky_numbers_for,
    matching_zodiacs_for,
    place_for,
    scent_for,
    stem_element,
    time_band_for,
)

_KST = timezone(timedelta(hours=9))


def _greeting(nickname: str) -> str:
    """반말 인사. 너무 가볍지 않게."""
    return f'"{nickname}, 오늘은 이런 식으로 흘러가면 좋아."'


def build_action_guide(user: User) -> Optional[dict]:
    """사주 기반 행동 가이드 dict 반환. {headline, tips: [{label, value}]}.

    user.birth_date 가 없으면 None.
    """
    if user.birth_date is None:
        return None

    try:
        saju = calculate_saju(user)
    except Exception:
        return None

    user_day = saju.pillars[2]
    user_day_stem = user_day.stem
    user_day_branch = user_day.branch
    el = saju.element_profile

    # 용신 추정 — 사주에서 가장 부족한 오행
    counts = {
        "wood": el.wood, "fire": el.fire, "earth": el.earth,
        "metal": el.metal, "water": el.water,
    }
    yongsin, _kisin = estimate_yongsin_kisin(counts)

    # 오늘 일진
    today_kst = datetime.now(_KST).date()
    today_stem, _today_branch = _day_pillar(today_kst)
    today_el = stem_element(today_stem)

    # 추천 기준: 용신 (사주 부족 오행) 우선, 없으면 오늘 일간 오행
    target = yongsin or today_el

    nickname = (user.nickname or "").strip() or "OOO"

    tips = []

    if target:
        tips.append({
            "label": "오늘의 컬러",
            "value": f"{color_for(target)} — 작은 포인트로 들이면 충분해",
        })
        tips.append({
            "label": "좋은 시간대",
            "value": f"{time_band_for(target)}에 활동량 올려봐",
        })
        tips.append({
            "label": "잘 어울리는 장소",
            "value": place_for(target),
        })
        tips.append({
            "label": "오늘의 의상 톤",
            "value": fashion_for(target),
        })
        tips.append({
            "label": "추천 향수",
            "value": scent_for(target),
        })
        tips.append({
            "label": "추천 음식",
            "value": food_for(target),
        })
        tips.append({
            "label": "추천 방위",
            "value": f"{direction_for(target)} 방향 자리 — 약속 잡을 때 참고해",
        })

    # 행운 숫자
    nums = lucky_numbers_for(user_day_stem)
    if nums:
        tips.append({
            "label": "행운의 숫자",
            "value": ", ".join(str(n) for n in nums),
        })

    # 잘 맞는 띠
    zodiacs = matching_zodiacs_for(user_day_branch)
    if zodiacs:
        tips.append({
            "label": "오늘 잘 맞는 띠",
            "value": " · ".join(zodiacs[:3]) + "띠",
        })

    return {
        "headline": _greeting(nickname),
        "tips": tips,
    }