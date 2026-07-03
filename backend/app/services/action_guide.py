"""사주 기반 오늘의 행동 가이드(옷차림/태도/마음가짐 3줄) 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.saju import calculate as calculate_saju
from app.services.saju_engine import _day_pillar
from app.services.saju_chart import ten_god, branch_ten_god
from app.services.saju_enrichment import (
    color_for,
    estimate_yongsin_kisin,
    fashion_for,
    korean_polite_name,
    stem_element,
)
from app.services.daily_ai import get_or_create_daily_text


_KST = timezone(timedelta(hours=9))


_ATTITUDE_BY_RELATION: dict[str, str] = {
    "비견": "익숙한 분들 사이에서 편안하게 본인 결을 보여주시면 좋겠어요",
    "겁재": "한 발 먼저 다가가는 적극적인 모습은 어떠신가요",
    "식신": "여유롭고 즐거운 분위기를 풍기시면 자연스럽게 호감이 따라올 거예요",
    "상관": "재치 있는 한마디는 좋지만, 너무 날 세우지 않게 부드럽게 표현해보세요",
    "정재": "차분하고 정성스러운 태도로 임해보시는 건 어떠신가요",
    "편재": "활발하고 사교적인 모습이 매력으로 빛날 수 있는 하루예요",
    "정관": "단정하고 신뢰감 있는 모습으로 임하시면 좋은 인상을 남기실 수 있어요",
    "편관": "당당하고 강단 있는 태도가 매력으로 다가갈 수 있는 하루입니다",
    "정인": "따뜻하고 배려 깊은 태도로 다가가시는 건 어떨까요",
    "편인": "평소와 살짝 다른 분위기를 보여주시면 더 매력적으로 보일 거예요",
    "—": "평소처럼 자연스럽게 임하시면 충분할 것 같아요",
}

_MOOD_BY_ELEMENT: dict[str, str] = {
    "wood":  "여유로운 마음으로 천천히 흐름을 따라가보시는 건 어떨까요",
    "fire":  "밝고 활기찬 에너지를 풍겨보시면 인상이 한층 좋아질 거예요",
    "earth": "안정감 있는 분위기로 차분히 대화에 집중해보세요",
    "metal": "깔끔하고 정돈된 분위기로 임하시면 신뢰감이 살아날 것 같아요",
    "water": "차분하고 깊이 있는 대화를 즐겨보시면 더 매력적이실 거예요",
}


def _mood_for(element: Optional[str]) -> str:
    if element is None:
        return "오늘은 마음의 흐름을 자연스럽게 따라가보시는 건 어떨까요"
    return _MOOD_BY_ELEMENT.get(
        element,
        "오늘은 마음의 흐름을 자연스럽게 따라가보시는 건 어떨까요",
    )


def build_action_guide(user: User) -> Optional[dict]:
    if user.birth_date is None:
        return None

    try:
        saju = calculate_saju(user)
    except Exception:
        return None

    user_day = saju.pillars[2]
    user_day_stem = user_day.stem
    el = saju.element_profile

    counts = {
        "wood": el.wood, "fire": el.fire, "earth": el.earth,
        "metal": el.metal, "water": el.water,
    }
    yongsin, _kisin = estimate_yongsin_kisin(counts)

    today_kst = datetime.now(_KST).date()
    today_stem, today_branch = _day_pillar(today_kst)
    today_el = stem_element(today_stem)
    target = yongsin or today_el

    relation = ten_god(user_day_stem, today_stem)
    if relation == "—":
        relation = branch_ten_god(user_day_stem, today_branch)

    raw_nickname = (user.nickname or "").strip() or "고객"
    call_name = korean_polite_name(raw_nickname)

    color = color_for(target) if target else "심플한 톤"
    fashion = fashion_for(target) if target else "심플한 룩"
    attitude = _ATTITUDE_BY_RELATION.get(relation, _ATTITUDE_BY_RELATION["—"])
    mood = _mood_for(target)

    text = (
        f'"{call_name}, 오늘은 {fashion} 톤에 {color} 포인트를 살짝 더해보시는 건 어떠신가요?\n'
        f"{attitude}.\n"
        f'{mood}."'
    )

    signal = (
        f"오늘의 십성: {relation} / 옷차림: {fashion}"
        f" / 태도: {attitude} / 마음가짐: {mood}"
    )

    return {"text": text, "signal": signal}

async def get_action_guide_ai(user: User, db: AsyncSession) -> Optional[dict]:
    guide = build_action_guide(user)
    if guide is None:
        return None
    ai = await get_or_create_daily_text(user, "action_guide", guide["signal"], db)
    return {"text": ai or guide["text"]}