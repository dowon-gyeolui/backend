"""사주 풀이 보조 룩업 — 도화살, 천을귀인, 충/합, 용신, 색상/방위 등.

`saju_chart.py` 의 천간/지지/십성 lookup 을 보완하는 풀이 데이터.
순수 데이터 + 결정론적 함수만 들어있어 단위 테스트가 쉽다.

이 모듈을 fortune.py / recommendations.py 에서 호출해 사용자별
풍부한 풀이를 생성한다.
"""

from __future__ import annotations

from typing import Literal, Optional

from app.services.saju_chart import (
    BRANCH_INFO,
    STEM_INFO,
    Branch,
    Element,
    Stem,
)

# --- 지지(地支) 충/합/형/파/해 -----------------------------------------
#
# 두 지지가 만났을 때 발생하는 관계. 인연운에서:
#   六沖(육충) — 정면 충돌, 갈등 위험
#   六合(육합) — 조화, 좋은 만남
#   三合(삼합) — 큰 합, 가장 길한 인연
#   三刑(삼형) — 형벌, 시비
#   六破(육파) — 깨짐, 약속 못 지킴
#   六害(육해) — 방해, 오해

# 六沖: 6쌍 (정반대 위치)
SIX_CLASH: dict[Branch, Branch] = {
    "자": "오", "오": "자",
    "축": "미", "미": "축",
    "인": "신", "신": "인",
    "묘": "유", "유": "묘",
    "진": "술", "술": "진",
    "사": "해", "해": "사",
}

# 六合: 6쌍
SIX_HARMONY: dict[Branch, Branch] = {
    "자": "축", "축": "자",
    "인": "해", "해": "인",
    "묘": "술", "술": "묘",
    "진": "유", "유": "진",
    "사": "신", "신": "사",
    "오": "미", "미": "오",
}

# 三合: 4그룹 — 같은 그룹 안에서 만나면 큰 합
TRINES: list[frozenset[Branch]] = [
    frozenset({"신", "자", "진"}),  # 수국 (water)
    frozenset({"해", "묘", "미"}),  # 목국 (wood)
    frozenset({"인", "오", "술"}),  # 화국 (fire)
    frozenset({"사", "유", "축"}),  # 금국 (metal)
]


def branch_relation(a: Branch, b: Branch) -> Literal["충", "합", "삼합", "동일", "보통"]:
    """두 지지 관계 한 단어로."""
    if a == b:
        return "동일"
    if SIX_CLASH.get(a) == b:
        return "충"
    if SIX_HARMONY.get(a) == b:
        return "합"
    for trine in TRINES:
        if a in trine and b in trine:
            return "삼합"
    return "보통"


# --- 도화살(桃花殺) ------------------------------------------------------
#
# 자/오/묘/유 — 도화의 4 지지. 사주에 이 지지가 있는 사람이 오늘 일지가
# 자기 도화에 해당하면 매력 발산 운, 새 인연이 들어오기 좋은 날.
#
# 정통 도화 산법은 년지 또는 일지의 삼합 그룹별로 도화 지지가 정해짐:
#   申子辰 그룹 → 도화 = 酉
#   亥卯未 그룹 → 도화 = 子
#   寅午戌 그룹 → 도화 = 卯
#   巳酉丑 그룹 → 도화 = 午

DOHWA_BY_TRINE: dict[frozenset[Branch], Branch] = {
    frozenset({"신", "자", "진"}): "유",
    frozenset({"해", "묘", "미"}): "자",
    frozenset({"인", "오", "술"}): "묘",
    frozenset({"사", "유", "축"}): "오",
}


def dohwa_branch_for(year_or_day_branch: Branch) -> Optional[Branch]:
    """주어진 지지의 도화 지지를 반환. 일치하지 않으면 None."""
    for trine, dohwa in DOHWA_BY_TRINE.items():
        if year_or_day_branch in trine:
            return dohwa
    return None


def is_dohwa_day(user_day_branch: Branch, today_branch: Branch) -> bool:
    """오늘이 사용자에게 도화 발동 일인지."""
    return dohwa_branch_for(user_day_branch) == today_branch


# --- 천을귀인(天乙貴人) -------------------------------------------------
#
# 일간별로 정해진 두 개의 길성 지지. 오늘 일지가 천을귀인이면 귀인을
# 만나거나 좋은 일이 생기는 길일.

CHEONEUL_GUIN: dict[Stem, tuple[Branch, Branch]] = {
    "갑": ("축", "미"),
    "무": ("축", "미"),
    "경": ("축", "미"),
    "을": ("자", "신"),
    "기": ("자", "신"),
    "병": ("해", "유"),
    "정": ("해", "유"),
    "임": ("사", "묘"),
    "계": ("사", "묘"),
    "신": ("인", "오"),
}


def is_cheoneul_day(user_day_stem: Stem, today_branch: Branch) -> bool:
    """오늘 일지가 사용자 일간 기준 천을귀인 지지인지."""
    return today_branch in CHEONEUL_GUIN.get(user_day_stem, ())


# --- 용신(用神) 추정 — 약식 ----------------------------------------------
#
# 정통 용신 산법은 격국론 + 강약 + 조후 등 복잡한 절차. 여기선 데이팅
# 앱 풀이용 휴리스틱:
#   "사주 8자 중 가장 부족한 오행 = 용신, 가장 많은 오행 = 기신"
# 정통은 아니지만 "내게 도움 되는 색상/방위" 같은 가이드용으론 충분.

ElementCount = dict[Element, int]


def estimate_yongsin_kisin(
    element_counts: ElementCount,
) -> tuple[Optional[Element], Optional[Element]]:
    """약식 용신/기신 추정. (용신, 기신).

    부족한 오행 = 용신, 과한 오행 = 기신.
    동률이면 None (확정 못 함).
    """
    if not element_counts:
        return None, None
    # 0인 오행 우선 후보, 없으면 최저값
    sorted_low = sorted(element_counts.items(), key=lambda kv: kv[1])
    sorted_high = sorted(element_counts.items(), key=lambda kv: -kv[1])
    yongsin = sorted_low[0][0] if sorted_low[0][1] < sorted_low[-1][1] else None
    kisin = sorted_high[0][0] if sorted_high[0][1] > sorted_high[-1][1] else None
    return yongsin, kisin


# --- 오행별 색상 / 방위 / 시간대 / 추천 -----------------------------------

ELEMENT_COLOR: dict[Element, list[str]] = {
    "wood":  ["초록", "청록", "라이트 그린"],
    "fire":  ["빨강", "오렌지", "코럴"],
    "earth": ["베이지", "황토", "카멜"],
    "metal": ["화이트", "실버", "아이보리"],
    "water": ["네이비", "블랙", "딥 블루"],
}

ELEMENT_DIRECTION: dict[Element, str] = {
    "wood":  "동쪽",
    "fire":  "남쪽",
    "earth": "중앙",
    "metal": "서쪽",
    "water": "북쪽",
}

ELEMENT_TIME_BAND: dict[Element, str] = {
    "wood":  "오전 (해 뜰 무렵)",
    "fire":  "정오 ~ 오후 (햇볕 강한 시간)",
    "earth": "오후 (해 기울 때)",
    "metal": "저녁 (해 질 무렵)",
    "water": "밤 (해 진 후)",
}

ELEMENT_PLACE: dict[Element, str] = {
    "wood":  "공원·숲·야외 산책로 같은 자연 가까운 곳",
    "fire":  "햇볕 잘 드는 야외 카페·활기찬 거리",
    "earth": "안정감 있는 한식당·아늑한 동네 카페",
    "metal": "깔끔한 갤러리·미술 전시·정돈된 공간",
    "water": "한적한 북카페·바다 가까운 곳·조용한 바",
}

ELEMENT_FOOD: dict[Element, str] = {
    "wood":  "샐러드·신선한 채소 요리",
    "fire":  "매콤한 음식·구이류",
    "earth": "한식·곡물 위주 식사",
    "metal": "깔끔한 일식·생선회",
    "water": "차가운 음료·해산물",
}

ELEMENT_SCENT: dict[Element, str] = {
    "wood":  "그린·우디 계열",
    "fire":  "스파이시·플로럴",
    "earth": "머스크·앰버",
    "metal": "프레시 시트러스",
    "water": "아쿠아·마린",
}

ELEMENT_FASHION: dict[Element, str] = {
    "wood":  "심플한 셔츠 룩, 자연스러운 컬러",
    "fire":  "포인트 컬러로 활기 있게",
    "earth": "베이지·카멜 톤의 안정감 있는 룩",
    "metal": "화이트·실버 깔끔한 미니멀",
    "water": "네이비·블랙 차분한 톤",
}

# 천간별 행운 숫자 (음양오행 기준)
STEM_LUCKY_NUMBER: dict[Stem, list[int]] = {
    "갑": [3, 8],   # 木+
    "을": [3, 8],   # 木-
    "병": [2, 7],   # 火+
    "정": [2, 7],   # 火-
    "무": [5, 10],  # 土+
    "기": [5, 10],  # 土-
    "경": [4, 9],   # 金+
    "신": [4, 9],   # 金-
    "임": [1, 6],   # 水+
    "계": [1, 6],   # 水-
}


def color_for(element: Element) -> str:
    """오행 → 추천 색상 (첫 번째)."""
    return ELEMENT_COLOR.get(element, ["흰색"])[0]


def direction_for(element: Element) -> str:
    return ELEMENT_DIRECTION.get(element, "동쪽")


def time_band_for(element: Element) -> str:
    return ELEMENT_TIME_BAND.get(element, "오후")


def place_for(element: Element) -> str:
    return ELEMENT_PLACE.get(element, "조용한 카페")


def food_for(element: Element) -> str:
    return ELEMENT_FOOD.get(element, "가벼운 식사")


def scent_for(element: Element) -> str:
    return ELEMENT_SCENT.get(element, "프레시 계열")


def fashion_for(element: Element) -> str:
    return ELEMENT_FASHION.get(element, "심플한 룩")


def lucky_numbers_for(stem: Stem) -> list[int]:
    return STEM_LUCKY_NUMBER.get(stem, [1, 7])


# --- 띠(zodiac) 합 ---------------------------------------------------------

# 일지(또는 년지) 기준 잘 맞는 띠 — 삼합·육합 가족.
def matching_zodiacs_for(branch: Branch) -> list[str]:
    """주어진 지지와 삼합 + 육합 으로 어울리는 동물 이름들."""
    matches: set[Branch] = set()
    for trine in TRINES:
        if branch in trine:
            matches.update(trine - {branch})
    if SIX_HARMONY.get(branch):
        matches.add(SIX_HARMONY[branch])
    return [BRANCH_INFO[b]["animal"] for b in matches if b in BRANCH_INFO]


# --- 지지의 오행 ---------------------------------------------------------

def branch_element(branch: Branch) -> Optional[Element]:
    info = BRANCH_INFO.get(branch)
    if info is None:
        return None
    return info.get("element")  # type: ignore[return-value]


def stem_element(stem: Stem) -> Optional[Element]:
    info = STEM_INFO.get(stem)
    if info is None:
        return None
    return info.get("element")  # type: ignore[return-value]


# --- 한국식 호칭 (성 떼고 받침 따라 야/아) -------------------------------

# 자주 쓰이는 한국 성씨. 가장 흔한 60여 개.
_ONE_CHAR_SURNAMES: set[str] = {
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
    "한", "오", "서", "신", "권", "황", "안", "송", "류", "전",
    "홍", "고", "문", "양", "손", "배", "백", "허", "유", "남",
    "심", "노", "하", "곽", "성", "차", "주", "우", "구", "원",
    "민", "나", "진", "지", "엄", "변", "채", "추", "도", "소",
    "석", "선", "설", "마", "길", "연", "위", "표", "명", "기",
    "반", "왕", "방", "옥", "육", "인", "맹", "제", "탁", "모",
}
_TWO_CHAR_SURNAMES: set[str] = {
    "남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방",
}


def _has_jongseong(ch: str) -> bool:
    """한글 한 글자의 받침 유무. 한글 아니면 False."""
    if not ch:
        return False
    code = ord(ch) - 0xAC00
    if not (0 <= code < 11172):
        return False
    return (code % 28) != 0


def korean_call_name(nickname: str) -> str:
    """닉네임에서 성을 떼고 받침 따라 호칭 어미("야" / "아") 붙임.

    예:
        "박양희" → "양희야"   (희 = 받침 없음)
        "김민수" → "민수야"   (수 = 받침 없음)
        "이지은" → "지은아"   (은 = 받침 있음)
        "황보석" → "석아"      (황보 = 2글자 성)
        "양희"   → "양희야"   (이미 2글자라 성 떼지 않음)
        ""       → ""
    """
    if not nickname:
        return ""
    name = nickname.strip()
    if not name:
        return ""

    # 3글자 이상이면 성 떼기 시도 (2글자 성 우선)
    if len(name) >= 3:
        if name[:2] in _TWO_CHAR_SURNAMES:
            name = name[2:]
        elif name[:1] in _ONE_CHAR_SURNAMES:
            name = name[1:]

    if not name:
        return ""

    suffix = "아" if _has_jongseong(name[-1]) else "야"
    return name + suffix


def korean_call_name_topic(nickname: str) -> str:
    """주격 호칭 — "양희는" / "지은이는" 같은 식.

    호칭 어미 없이 이름 자체만 반환 (성 제거된 형태).
    """
    if not nickname:
        return ""
    name = nickname.strip()
    if len(name) >= 3:
        if name[:2] in _TWO_CHAR_SURNAMES:
            name = name[2:]
        elif name[:1] in _ONE_CHAR_SURNAMES:
            name = name[1:]
    return name
