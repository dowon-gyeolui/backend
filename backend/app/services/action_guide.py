"""오늘의 행동 가이드 — 사주 기반 동적 행동 추천 (3줄 산문).

인연운(fortune.py) 과 차별화:
  - 인연운: "오늘 어떤 만남/장소/시간이 좋은가" (운세 관점)
  - 행동 가이드: "오늘 어떻게 입고 / 어떤 태도로 / 어떤 마음으로
    임할까" (행동 관점)

3줄 자연스러운 글로 출력. 항목 박스 없음. 닉네임은 성 떼고 받침
따라 야/아 호칭.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.user import User
from app.services.saju import calculate as calculate_saju
from app.services.saju_engine import _day_pillar
from app.services.saju_chart import ten_god, branch_ten_god
from app.services.saju_enrichment import (
    color_for,
    estimate_yongsin_kisin,
    fashion_for,
    korean_call_name,
    stem_element,
)

_KST = timezone(timedelta(hours=9))


# 오늘의 십성 관계별 "행동 톤". 인연운 본문과 겹치지 않게,
# 옷차림/태도/마음가짐 관점으로만 작성. 모두 반말.
_ATTITUDE_BY_RELATION: dict[str, str] = {
    "비견": "익숙한 사람들 사이에서 편안하게 본인 결을 보여주면 돼",
    "겁재": "한 발 먼저 다가가는 적극성이 매력으로 다가갈 거야",
    "식신": "여유롭고 즐거운 분위기를 풍기면 자연스럽게 호감이 따라와",
    "상관": "재치 있는 한마디는 좋은데, 너무 날 세우지는 말고 부드럽게",
    "정재": "차분하고 정성스러운 태도가 가장 잘 받아들여지는 날이야",
    "편재": "활발하고 사교적인 모습이 매력으로 빛나는 하루",
    "정관": "단정하고 신뢰감 있는 모습으로 임하면 좋은 인상을 남겨",
    "편관": "당당하고 강단 있는 태도가 강한 매력으로 다가가는 하루",
    "정인": "따뜻하고 배려 깊은 태도가 마음을 움직이는 날이야",
    "편인": "평소와 다른 분위기를 살짝 보여주면 더 매력적으로 보여",
    "—": "평소처럼 자연스럽게 임하면 충분해",
}

# 오행별 행동 분위기 (마음가짐) — 행동 가이드용 별도 매핑.
_MOOD_BY_ELEMENT: dict[str, str] = {
    "wood":  "여유로운 마음으로 천천히 흐름을 따라가봐",
    "fire":  "밝고 활기찬 에너지를 풍기면 인상이 한층 좋아져",
    "earth": "안정감 있는 분위기로 차분히 대화에 집중해",
    "metal": "깔끔하고 정돈된 분위기로 임하면 신뢰감이 살아나",
    "water": "차분하고 깊이 있는 대화를 즐기면 더 매력적이야",
}


def _mood_for(element: Optional[str]) -> str:
    if element is None:
        return "오늘 마음의 흐름을 자연스럽게 따라가봐"
    return _MOOD_BY_ELEMENT.get(element, "오늘 마음의 흐름을 자연스럽게 따라가봐")


def build_action_guide(user: User) -> Optional[dict]:
    """사주 기반 행동 가이드 — 3줄 산문 반환.

    응답: {"text": "<3줄 글>"}
    """
    if user.birth_date is None:
        return None

    try:
        saju = calculate_saju(user)
    except Exception:
        return None

    user_day = saju.pillars[2]
    user_day_stem = user_day.stem
    el = saju.element_profile

    # 용신 추정
    counts = {
        "wood": el.wood, "fire": el.fire, "earth": el.earth,
        "metal": el.metal, "water": el.water,
    }
    yongsin, _kisin = estimate_yongsin_kisin(counts)

    # 오늘 일간
    today_kst = datetime.now(_KST).date()
    today_stem, today_branch = _day_pillar(today_kst)
    today_el = stem_element(today_stem)
    target = yongsin or today_el

    # 사용자 일간 ↔ 오늘 일간 십성 (행동 톤 결정)
    relation = ten_god(user_day_stem, today_stem)
    if relation == "—":
        relation = branch_ten_god(user_day_stem, today_branch)

    raw_nickname = (user.nickname or "").strip() or "친구"
    call_name = korean_call_name(raw_nickname)

    color = color_for(target) if target else "심플한 톤"
    fashion = fashion_for(target) if target else "심플한 룩"
    attitude = _ATTITUDE_BY_RELATION.get(relation, _ATTITUDE_BY_RELATION["—"])
    mood = _mood_for(target)

    # 3줄 자연스러운 산문:
    # Line 1: 옷차림 (의상 + 컬러 포인트)
    # Line 2: 태도 (관계별 행동 톤)
    # Line 3: 마음가짐 / 분위기
    text = (
        f'"{call_name}, 오늘은 {fashion}, {color} 포인트 하나 더해서 입어봐.\n'
        f"{attitude}.\n"
        f'{mood}."'
    )

    return {"text": text}