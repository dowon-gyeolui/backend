"""사주 풀이 보조 룩업 — 도화살/천을귀인/충·합/용신/색상·방위."""

from __future__ import annotations

from typing import Literal, Optional

from app.services.saju_chart import (
    BRANCH_INFO,
    STEM_INFO,
    Branch,
    Element,
    Stem,
)

SIX_CLASH: dict[Branch, Branch] = {
    "자": "오", "오": "자",
    "축": "미", "미": "축",
    "인": "신", "신": "인",
    "묘": "유", "유": "묘",
    "진": "술", "술": "진",
    "사": "해", "해": "사",
}

SIX_HARMONY: dict[Branch, Branch] = {
    "자": "축", "축": "자",
    "인": "해", "해": "인",
    "묘": "술", "술": "묘",
    "진": "유", "유": "진",
    "사": "신", "신": "사",
    "오": "미", "미": "오",
}

TRINES: list[frozenset[Branch]] = [
    frozenset({"신", "자", "진"}),
    frozenset({"해", "묘", "미"}),
    frozenset({"인", "오", "술"}),
    frozenset({"사", "유", "축"}),
]


def branch_relation(a: Branch, b: Branch) -> Literal["충", "합", "삼합", "동일", "보통"]:
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


DOHWA_BY_TRINE: dict[frozenset[Branch], Branch] = {
    frozenset({"신", "자", "진"}): "유",
    frozenset({"해", "묘", "미"}): "자",
    frozenset({"인", "오", "술"}): "묘",
    frozenset({"사", "유", "축"}): "오",
}


def dohwa_branch_for(year_or_day_branch: Branch) -> Optional[Branch]:
    for trine, dohwa in DOHWA_BY_TRINE.items():
        if year_or_day_branch in trine:
            return dohwa
    return None


def is_dohwa_day(user_day_branch: Branch, today_branch: Branch) -> bool:
    return dohwa_branch_for(user_day_branch) == today_branch


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
    return today_branch in CHEONEUL_GUIN.get(user_day_stem, ())


ElementCount = dict[Element, int]


def estimate_yongsin_kisin(
    element_counts: ElementCount,
) -> tuple[Optional[Element], Optional[Element]]:
    if not element_counts:
        return None, None
    sorted_low = sorted(element_counts.items(), key=lambda kv: kv[1])
    sorted_high = sorted(element_counts.items(), key=lambda kv: -kv[1])
    yongsin = sorted_low[0][0] if sorted_low[0][1] < sorted_low[-1][1] else None
    kisin = sorted_high[0][0] if sorted_high[0][1] > sorted_high[-1][1] else None
    return yongsin, kisin


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

STEM_LUCKY_NUMBER: dict[Stem, list[int]] = {
    "갑": [3, 8],
    "을": [3, 8],
    "병": [2, 7],
    "정": [2, 7],
    "무": [5, 10],
    "기": [5, 10],
    "경": [4, 9],
    "신": [4, 9],
    "임": [1, 6],
    "계": [1, 6],
}


def color_for(element: Element) -> str:
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


def matching_zodiacs_for(branch: Branch) -> list[str]:
    matches: set[Branch] = set()
    for trine in TRINES:
        if branch in trine:
            matches.update(trine - {branch})
    if SIX_HARMONY.get(branch):
        matches.add(SIX_HARMONY[branch])
    return [BRANCH_INFO[b]["animal"] for b in matches if b in BRANCH_INFO]


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
    if not ch:
        return False
    code = ord(ch) - 0xAC00
    if not (0 <= code < 11172):
        return False
    return (code % 28) != 0


def _strip_surname(name: str) -> str:
    if len(name) >= 3:
        if name[:2] in _TWO_CHAR_SURNAMES:
            return name[2:]
        if name[:1] in _ONE_CHAR_SURNAMES:
            return name[1:]
    return name


def korean_call_name(nickname: str) -> str:
    if not nickname:
        return ""
    name = _strip_surname(nickname.strip())
    if not name:
        return ""
    suffix = "아" if _has_jongseong(name[-1]) else "야"
    return name + suffix


def korean_polite_name(nickname: str) -> str:
    if not nickname:
        return ""
    name = _strip_surname(nickname.strip())
    if not name:
        return ""
    return name + "님"


def korean_call_name_topic(nickname: str) -> str:
    if not nickname:
        return ""
    return _strip_surname(nickname.strip())