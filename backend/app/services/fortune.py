"""오늘의 인연운 — 사용자 사주 + 오늘 일진(日辰) 기반 일일 fortune.

매일 KST 자정에 사실상 변경됨 (오늘의 일주가 매일 다르므로 ten-god
관계도 매일 달라짐). 같은 사용자가 같은 날 여러 번 호출해도 동일한
문구 반환 (date+user_id seed 로 결정론적 선택).

LLM 호출 없음 — rule-based 템플릿 풀에서 deterministic 선택. 비용 0,
응답 즉시. 추후 LLM 으로 풀이 풍부하게 만들 여지는 둠.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.models.user import User
from app.services.saju import calculate as calculate_saju
from app.services.saju_chart import (
    BRANCH_INFO,
    ELEMENT_COLOR_KO,
    STEM_INFO,
    branch_ten_god,
    ten_god,
)
from app.services.saju_engine import _day_pillar

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


@dataclass
class TodayFortune:
    """API 응답 — 오늘의 인연운."""

    fortune_text: str       # 사용자에게 노출되는 한국어 문구
    today_pillar: str       # 오늘의 일주 (예: "갑진")
    today_pillar_hanja: str # 한자 (예: "甲辰")
    relation: str           # 사용자 일간 ↔ 오늘 일간의 십성 관계
    element_today: str      # 오늘 일간의 오행 (한국어, 예: "목")
    score: int              # 1~5 — 인연 운 강도


# --- 템플릿 풀 ---------------------------------------------------------
#
# 각 십성 관계별로 3~4개 문구. 모두 2 줄 — 첫 줄은 상황 묘사, 두번째
# 줄은 행동 권유 (Tinder/Bumble 의 "today's mood" 톤). {nickname} 으로
# 사용자 닉네임 치환.

_TEMPLATES_BY_RELATION: dict[str, list[str]] = {
    "비견": [
        '"{nickname}님, 오늘은 친구나 지인을 통해 새로운 인연이 닿을 수 있어요.\n익숙한 자리에 한 번 더 나가보는 건 어때요?"',
        '"{nickname}님, 비슷한 결을 가진 사람과의 대화가 깊어지는 하루예요.\n자연스러운 만남을 즐겨보세요."',
        '"{nickname}님, 오늘은 동료·친구와의 시간이 의외의 인연으로 이어질 수 있어요."',
    ],
    "겁재": [
        '"{nickname}님, 적극적인 행동이 좋은 결과를 가져오는 날이에요.\n마음에 두고 있는 사람에게 먼저 연락해볼까요?"',
        '"{nickname}님, 오늘은 경쟁심과 자신감이 매력으로 비춰져요.\n망설이지 말고 다가가보세요."',
        '"{nickname}님, 평소보다 한 발 더 나가는 용기가 인연을 부릅니다."',
    ],
    "식신": [
        '"{nickname}님, 표현력이 빛나는 하루 — 평소 말 못 했던 마음을 전해보세요.\n작은 칭찬 한마디가 인연의 시작이 될 수 있어요."',
        '"{nickname}님, 오늘은 즐거운 분위기 속에서 인연이 깊어지는 운이 좋아요.\n웃음이 많은 자리에 가보세요."',
        '"{nickname}님, 따뜻한 말 한마디가 평소보다 더 큰 효과를 내는 날이에요."',
    ],
    "상관": [
        '"{nickname}님, 재치 있는 대화가 호감을 끌어내는 날이에요.\n다만 너무 직설적이지 않게, 부드럽게 표현해보세요."',
        '"{nickname}님, 솔직함이 매력으로 다가가는 하루예요.\n진심을 담아 이야기해보세요."',
        '"{nickname}님, 오늘은 평소와 다른 색다른 모습이 인연을 끌어옵니다."',
    ],
    "정재": [
        '"{nickname}님은 오늘 운명의 상대를 만날 확률이 높아요!\n맘에 두고 있는 사람이 있다면 표현해볼까요?"',
        '"{nickname}님, 차분하게 다가가면 안정적인 관계로 발전할 수 있는 하루예요."',
        '"{nickname}님, 이성과의 만남에 좋은 운이 따르는 날 — 평소보다 한층 더 정성을 들여보세요."',
    ],
    "편재": [
        '"{nickname}님, 활발한 사교 활동이 새로운 인연을 끌어오는 하루예요.\n오늘은 적극적인 만남이 좋은 결과를 만듭니다."',
        '"{nickname}님, 다양한 사람과의 만남이 좋은 운으로 이어지는 날이에요.\n낯선 자리에도 한 번 가보세요."',
        '"{nickname}님, 오늘은 직감이 잘 맞는 날 — 끌리는 만남을 신뢰해보세요."',
    ],
    "정관": [
        '"{nickname}님, 책임감 있는 모습이 매력으로 보이는 하루예요.\n안정적이고 진중한 인연이 다가올 수 있어요."',
        '"{nickname}님, 오늘은 신뢰감을 주는 행동이 좋은 평가를 받습니다.\n단정한 모습으로 만남에 임해보세요."',
        '"{nickname}님, 오랫동안 함께할 인연이 다가오는 신호가 보여요."',
    ],
    "편관": [
        '"{nickname}님, 강한 카리스마가 돋보이는 하루 — 자신감 있게 행동하세요.\n도전적인 만남이 새로운 가능성을 열어줄 수 있어요."',
        '"{nickname}님, 오늘은 평소와 다른 결단력이 매력으로 다가갑니다.\n망설이던 결정을 내려보세요."',
        '"{nickname}님, 강렬한 첫 인상이 통하는 하루 — 적극적으로 나서보세요."',
    ],
    "정인": [
        '"{nickname}님, 오늘은 따뜻한 마음이 좋은 인연을 부르는 하루예요.\n주변의 도움이 인연으로 연결될 수 있어요."',
        '"{nickname}님, 학습·취미 자리에서 깊은 인연이 시작될 수 있는 날이에요.\n관심 있는 모임에 나가보세요."',
        '"{nickname}님, 오늘은 차분한 만남이 마음을 깊이 움직이는 날입니다."',
    ],
    "편인": [
        '"{nickname}님, 직관이 예민해지는 날 — 끌리는 사람을 믿어보세요.\n평소와 다른 만남이 의외의 인연이 될 수 있어요."',
        '"{nickname}님, 독특한 매력이 빛나는 하루 — 평소와 다른 모습을 보여주세요.\n색다른 자리에 도전해볼까요?"',
        '"{nickname}님, 오늘은 통찰이 깊어지는 날 — 사람을 보는 눈이 더 정확해져요."',
    ],
    # 십성 계산이 실패한 케이스 (오행 연결 불명확) — 일반 fallback
    "—": [
        '"{nickname}님, 오늘은 평소와 같이 자연스러운 모습으로 임해보세요.\n작은 인사 한마디가 큰 인연으로 이어질 수 있어요."',
        '"{nickname}님, 오늘은 마음의 흐름을 따라가는 하루로 보내보세요.\n좋은 인연은 자연스럽게 다가옵니다."',
    ],
}


# 십성별 인연 운 강도 (1~5). 인연 운 관점에서:
#   정재·편재 — 직접적인 이성/재물 운 ★★★★★
#   식신·상관 — 표현/매력 ★★★★
#   정관·편관 — 안정/카리스마 ★★★★
#   정인·편인 — 따뜻함/통찰 ★★★
#   비견·겁재 — 친구·동료 운 ★★★
_RELATION_SCORE: dict[str, int] = {
    "정재": 5, "편재": 5,
    "식신": 4, "상관": 4,
    "정관": 4, "편관": 4,
    "정인": 3, "편인": 3,
    "비견": 3, "겁재": 3,
    "—": 3,
}


def today_day_pillar_kst() -> tuple[str, str]:
    """KST 자정 기준 오늘의 일주 (천간, 지지)."""
    today = datetime.now(_KST).date()
    return _day_pillar(today)


def _hanja_pillar(stem: str, branch: str) -> str:
    s = STEM_INFO.get(stem, {}).get("hanja", "")
    b = BRANCH_INFO.get(branch, {}).get("hanja", "")
    return f"{s}{b}"


def compute_today_fortune(user: User) -> Optional[TodayFortune]:
    """사용자 사주 + 오늘 일진 → 인연운 텍스트 + 메타.

    user.birth_date 가 없으면 None 반환 (호출자가 적절히 처리).
    """
    if user.birth_date is None:
        return None

    # 사용자의 일간 (일주의 천간)
    try:
        user_saju = calculate_saju(user)
        user_day_stem = user_saju.pillars[2].stem
    except Exception as e:
        logger.warning("calculate_saju failed for user %s: %s", user.id, e)
        return None

    # 오늘의 일주 (KST 기준)
    today_kst = datetime.now(_KST).date()
    today_stem, today_branch = _day_pillar(today_kst)

    # 사용자 일간 입장에서 오늘 일간이 어떤 십성인가
    relation = ten_god(user_day_stem, today_stem)
    # 만약 천간 ten-god 가 명확치 않으면 지지(today_branch) 기준으로 fallback
    if relation == "—":
        relation = branch_ten_god(user_day_stem, today_branch)

    # 템플릿 선택 — 날짜 + user_id 시드. 같은 날 같은 사용자는 항상
    # 동일 문구. 다른 사용자도 자기만의 문구를 받음.
    pool = _TEMPLATES_BY_RELATION.get(relation, _TEMPLATES_BY_RELATION["—"])
    seed = today_kst.toordinal() ^ (user.id * 2654435761) & 0xFFFFFFFF
    template = pool[seed % len(pool)]

    nickname = (user.nickname or "").strip() or "OOO"
    text = template.format(nickname=nickname)

    # 오늘 일간의 오행
    today_element_en = STEM_INFO.get(today_stem, {}).get("element", "")
    today_element_ko = (
        ELEMENT_COLOR_KO.get(today_element_en, "")  # type: ignore[arg-type]
        if today_element_en
        else ""
    )
    # ELEMENT_COLOR_KO 는 "푸른"/"붉은" 같은 색 단어라 — 오행 한글 단어가
    # 필요하면 별도 매핑.
    _ELEMENT_KO_NAME = {
        "wood": "목", "fire": "화", "earth": "토",
        "metal": "금", "water": "수",
    }
    element_today_ko = _ELEMENT_KO_NAME.get(today_element_en, "")

    return TodayFortune(
        fortune_text=text,
        today_pillar=f"{today_stem}{today_branch}",
        today_pillar_hanja=_hanja_pillar(today_stem, today_branch),
        relation=relation,
        element_today=element_today_ko,
        score=_RELATION_SCORE.get(relation, 3),
    )