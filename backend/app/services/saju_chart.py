"""사주 명식(命式) lookup tables — 십성·지장간·12운성·12신살.

Self-contained pure Python; called from `services.saju.calculate()` to
enrich each pillar with the rows shown in a traditional 명식 chart.
"""

from __future__ import annotations

from typing import Literal

# --- 천간 (Heavenly Stems) ---------------------------------------------

Stem = str  # one of "갑"·"을"·...·"계"
Branch = str  # one of "자"·"축"·...·"해"
Element = Literal["wood", "fire", "earth", "metal", "water"]
Polarity = Literal["+", "-"]

# 천간 → 한자, 오행, 음양
STEM_INFO: dict[Stem, dict[str, str]] = {
    "갑": {"hanja": "甲", "element": "wood",  "polarity": "+"},
    "을": {"hanja": "乙", "element": "wood",  "polarity": "-"},
    "병": {"hanja": "丙", "element": "fire",  "polarity": "+"},
    "정": {"hanja": "丁", "element": "fire",  "polarity": "-"},
    "무": {"hanja": "戊", "element": "earth", "polarity": "+"},
    "기": {"hanja": "己", "element": "earth", "polarity": "-"},
    "경": {"hanja": "庚", "element": "metal", "polarity": "+"},
    "신": {"hanja": "辛", "element": "metal", "polarity": "-"},
    "임": {"hanja": "壬", "element": "water", "polarity": "+"},
    "계": {"hanja": "癸", "element": "water", "polarity": "-"},
}

# 지지 → 한자, 동물, 오행, 음양 (체용 표기 — 사주 명리학 통상치)
BRANCH_INFO: dict[Branch, dict[str, str]] = {
    "자": {"hanja": "子", "animal": "쥐",     "element": "water", "polarity": "+"},
    "축": {"hanja": "丑", "animal": "소",     "element": "earth", "polarity": "-"},
    "인": {"hanja": "寅", "animal": "범",     "element": "wood",  "polarity": "+"},
    "묘": {"hanja": "卯", "animal": "토끼",   "element": "wood",  "polarity": "-"},
    "진": {"hanja": "辰", "animal": "용",     "element": "earth", "polarity": "+"},
    "사": {"hanja": "巳", "animal": "뱀",     "element": "fire",  "polarity": "+"},
    "오": {"hanja": "午", "animal": "말",     "element": "fire",  "polarity": "-"},
    "미": {"hanja": "未", "animal": "양",     "element": "earth", "polarity": "-"},
    "신": {"hanja": "申", "animal": "원숭이", "element": "metal", "polarity": "+"},
    "유": {"hanja": "酉", "animal": "닭",     "element": "metal", "polarity": "-"},
    "술": {"hanja": "戌", "animal": "개",     "element": "earth", "polarity": "+"},
    "해": {"hanja": "亥", "animal": "돼지",   "element": "water", "polarity": "-"},
}

# 일주 한자 색상 (天干의 오행 색)
ELEMENT_COLOR_KO: dict[Element, str] = {
    "wood":  "푸른",
    "fire":  "붉은",
    "earth": "노란",
    "metal": "흰",
    "water": "검은",
}


# --- 십성 (Ten Gods) ---------------------------------------------------

# 오행 상생 (a → b 면 a 가 b 를 生)
PRODUCES: dict[Element, Element] = {
    "wood": "fire", "fire": "earth", "earth": "metal",
    "metal": "water", "water": "wood",
}

# 오행 상극 (a → b 면 a 가 b 를 剋)
CONTROLS: dict[Element, Element] = {
    "wood": "earth", "earth": "water", "water": "fire",
    "fire": "metal", "metal": "wood",
}


def ten_god(day_stem: Stem, target_stem: Stem) -> str:
    """일간(day_stem) 기준 target_stem 의 십성 이름 반환."""
    day = STEM_INFO[day_stem]
    tgt = STEM_INFO[target_stem]
    same_polarity = day["polarity"] == tgt["polarity"]
    de, te = day["element"], tgt["element"]

    if de == te:
        return "비견" if same_polarity else "겁재"
    if PRODUCES.get(de) == te:
        return "식신" if same_polarity else "상관"
    if CONTROLS.get(de) == te:
        return "편재" if same_polarity else "정재"
    if CONTROLS.get(te) == de:
        return "편관" if same_polarity else "정관"
    if PRODUCES.get(te) == de:
        return "편인" if same_polarity else "정인"
    return "—"


def branch_ten_god(day_stem: Stem, branch: Branch) -> str:
    """지지의 본기(주된 element/polarity) 를 기준으로 십성을 계산."""
    info = BRANCH_INFO[branch]
    # 임시로 같은 element + polarity 의 천간을 찾아 ten_god() 재사용
    for stem, data in STEM_INFO.items():
        if data["element"] == info["element"] and data["polarity"] == info["polarity"]:
            return ten_god(day_stem, stem)
    return "—"


# --- 지장간 (Hidden Stems) --------------------------------------------

# 정기를 마지막에 두는 통상 표기
HIDDEN_STEMS: dict[Branch, list[Stem]] = {
    "자": ["임", "계"],
    "축": ["계", "신", "기"],
    "인": ["무", "병", "갑"],
    "묘": ["갑", "을"],
    "진": ["을", "계", "무"],
    "사": ["무", "경", "병"],
    "오": ["병", "기", "정"],
    "미": ["정", "을", "기"],
    "신": ["무", "임", "경"],
    "유": ["경", "신"],
    "술": ["신", "정", "무"],
    "해": ["무", "갑", "임"],
}


# --- 12운성 (12 Stages of Life) ----------------------------------------

# 일간 -> 지지 -> 12운성 단계
# 양간(갑·병·무·경·임)은 順行, 음간(을·정·기·신·계)은 逆行.
TWELVE_STAGES: dict[Stem, dict[Branch, str]] = {
    "갑": {"해": "장생", "자": "목욕", "축": "관대", "인": "건록", "묘": "제왕",
            "진": "쇠",   "사": "병",   "오": "사",   "미": "묘",   "신": "절",
            "유": "태",   "술": "양"},
    "을": {"오": "장생", "사": "목욕", "진": "관대", "묘": "건록", "인": "제왕",
            "축": "쇠",   "자": "병",   "해": "사",   "술": "묘",   "유": "절",
            "신": "태",   "미": "양"},
    "병": {"인": "장생", "묘": "목욕", "진": "관대", "사": "건록", "오": "제왕",
            "미": "쇠",   "신": "병",   "유": "사",   "술": "묘",   "해": "절",
            "자": "태",   "축": "양"},
    "정": {"유": "장생", "신": "목욕", "미": "관대", "오": "건록", "사": "제왕",
            "진": "쇠",   "묘": "병",   "인": "사",   "축": "묘",   "자": "절",
            "해": "태",   "술": "양"},
    "무": {"인": "장생", "묘": "목욕", "진": "관대", "사": "건록", "오": "제왕",
            "미": "쇠",   "신": "병",   "유": "사",   "술": "묘",   "해": "절",
            "자": "태",   "축": "양"},
    "기": {"유": "장생", "신": "목욕", "미": "관대", "오": "건록", "사": "제왕",
            "진": "쇠",   "묘": "병",   "인": "사",   "축": "묘",   "자": "절",
            "해": "태",   "술": "양"},
    "경": {"사": "장생", "오": "목욕", "미": "관대", "신": "건록", "유": "제왕",
            "술": "쇠",   "해": "병",   "자": "사",   "축": "묘",   "인": "절",
            "묘": "태",   "진": "양"},
    "신": {"자": "장생", "해": "목욕", "술": "관대", "유": "건록", "신": "제왕",
            "미": "쇠",   "오": "병",   "사": "사",   "진": "묘",   "묘": "절",
            "인": "태",   "축": "양"},
    "임": {"신": "장생", "유": "목욕", "술": "관대", "해": "건록", "자": "제왕",
            "축": "쇠",   "인": "병",   "묘": "사",   "진": "묘",   "사": "절",
            "오": "태",   "미": "양"},
    "계": {"묘": "장생", "인": "목욕", "축": "관대", "자": "건록", "해": "제왕",
            "술": "쇠",   "유": "병",   "신": "사",   "미": "묘",   "오": "절",
            "사": "태",   "진": "양"},
}


def twelve_stage(day_stem: Stem, branch: Branch) -> str:
    return TWELVE_STAGES.get(day_stem, {}).get(branch, "—")


# --- 12신살 (12 Spirits) ----------------------------------------------

# 년지가 속한 삼합 그룹별 12신살 매핑.
# 각 그룹의 키는 그 그룹에 속하는 지지 3개 (예: 인오술生 → "fire" 그룹).
TRIPLET_OF_BRANCH: dict[Branch, str] = {
    # 寅午戌 = 火局
    "인": "fire", "오": "fire", "술": "fire",
    # 申子辰 = 水局
    "신": "water", "자": "water", "진": "water",
    # 巳酉丑 = 金局
    "사": "metal", "유": "metal", "축": "metal",
    # 亥卯未 = 木局
    "해": "wood", "묘": "wood", "미": "wood",
}

# 그룹 → 지지 → 12신살 이름
# 출처: 표준 명리학 12신살 표 (年支 기준)
TWELVE_SPIRITS: dict[str, dict[Branch, str]] = {
    "fire": {
        "해": "겁살", "자": "재살", "축": "천살", "인": "지살",
        "묘": "도화살", "진": "월살", "사": "망신살", "오": "장성살",
        "미": "반안살", "신": "역마살", "유": "육해살", "술": "화개살",
    },
    "water": {
        "사": "겁살", "오": "재살", "미": "천살", "신": "지살",
        "유": "도화살", "술": "월살", "해": "망신살", "자": "장성살",
        "축": "반안살", "인": "역마살", "묘": "육해살", "진": "화개살",
    },
    "metal": {
        "인": "겁살", "묘": "재살", "진": "천살", "사": "지살",
        "오": "도화살", "미": "월살", "신": "망신살", "유": "장성살",
        "술": "반안살", "해": "역마살", "자": "육해살", "축": "화개살",
    },
    "wood": {
        "신": "겁살", "유": "재살", "술": "천살", "해": "지살",
        "자": "도화살", "축": "월살", "인": "망신살", "묘": "장성살",
        "진": "반안살", "사": "역마살", "오": "육해살", "미": "화개살",
    },
}


def twelve_spirit(year_branch: Branch, target_branch: Branch) -> str:
    """년지 기준의 12신살 이름 반환."""
    triplet = TRIPLET_OF_BRANCH.get(year_branch)
    if triplet is None:
        return "—"
    return TWELVE_SPIRITS[triplet].get(target_branch, "—")