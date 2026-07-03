"""정통 60갑자 사주 엔진 — 절기/60갑자/五虎遁/五鼠遁/음력 변환."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional

from korean_lunar_calendar import KoreanLunarCalendar


STEMS = ["갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"]
BRANCHES = ["자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"]

STEM_ELEMENT: dict[str, str] = {
    "갑": "wood", "을": "wood",
    "병": "fire", "정": "fire",
    "무": "earth", "기": "earth",
    "경": "metal", "신": "metal",
    "임": "water", "계": "water",
}

BRANCH_ELEMENT: dict[str, str] = {
    "인": "wood", "묘": "wood",
    "사": "fire", "오": "fire",
    "진": "earth", "축": "earth", "술": "earth", "미": "earth",
    "신": "metal", "유": "metal",
    "해": "water", "자": "water",
}


_JEOLGI_TO_BRANCH = [
    (1, 6, "축"),
    (2, 4, "인"),
    (3, 6, "묘"),
    (4, 5, "진"),
    (5, 6, "사"),
    (6, 6, "오"),
    (7, 7, "미"),
    (8, 8, "신"),
    (9, 8, "유"),
    (10, 8, "술"),
    (11, 7, "해"),
    (12, 7, "자"),
]


def _month_branch(d: date) -> str:
    md = (d.month, d.day)
    for i, (jm, jd, branch) in enumerate(_JEOLGI_TO_BRANCH):
        next_idx = (i + 1) % 12
        next_m, next_d, _ = _JEOLGI_TO_BRANCH[next_idx]
        if i < 11:
            if (jm, jd) <= md < (next_m, next_d):
                return branch
        else:
            if md >= (jm, jd) or md < (1, 6):
                return branch
    return "자"


def _year_pillar(d: date) -> tuple[str, str]:
    year = d.year
    if (d.month, d.day) < (2, 4):
        year -= 1
    stem = STEMS[(year - 4) % 10]
    branch = BRANCHES[(year - 4) % 12]
    return stem, branch


def _month_pillar(d: date, year_stem: str) -> tuple[str, str]:
    branch = _month_branch(d)
    in_month_stem_table = {
        "갑": "병", "기": "병",
        "을": "무", "경": "무",
        "병": "경", "신": "경",
        "정": "임", "임": "임",
        "무": "갑", "계": "갑",
    }
    in_stem = in_month_stem_table[year_stem]
    in_stem_idx = STEMS.index(in_stem)
    branch_order_from_in = ["인", "묘", "진", "사", "오", "미",
                            "신", "유", "술", "해", "자", "축"]
    offset = branch_order_from_in.index(branch)
    stem = STEMS[(in_stem_idx + offset) % 10]
    return stem, branch


_DAY_REF_DATE = date(1900, 1, 31)
_DAY_REF_STEM_IDX = 0
_DAY_REF_BRANCH_IDX = 4


def _day_pillar(d: date) -> tuple[str, str]:
    days = (d - _DAY_REF_DATE).days
    stem = STEMS[(_DAY_REF_STEM_IDX + days) % 10]
    branch = BRANCHES[(_DAY_REF_BRANCH_IDX + days) % 12]
    return stem, branch


_BIRTH_PLACE_OFFSET_MIN: dict[str, int] = {
    "서울특별시":   -32,
    "인천광역시":   -33,
    "경기도":       -32,
    "강원도":       -29,
    "강원특별자치도": -29,
    "충청북도":     -31,
    "충청남도":     -32,
    "대전광역시":   -32,
    "세종특별자치시": -32,
    "전라북도":     -33,
    "전북특별자치도": -33,
    "전라남도":     -33,
    "광주광역시":   -33,
    "경상북도":     -29,
    "대구광역시":   -29,
    "경상남도":     -27,
    "부산광역시":   -27,
    "울산광역시":   -27,
    "제주특별자치도": -34,
    "해외/기타":    0,
}


def _adjust_birth_time(birth_time: str, birth_place: Optional[str]) -> str:
    offset = _BIRTH_PLACE_OFFSET_MIN.get(
        birth_place or "",
        -32 if not birth_place else 0,
    )
    if offset == 0:
        return birth_time
    try:
        hh, mm = birth_time.split(":")
        total = int(hh) * 60 + int(mm) + offset
        total %= 24 * 60
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, AttributeError):
        return birth_time


def _time_pillar(birth_time: Optional[str], day_stem: str) -> Optional[tuple[str, str]]:
    if not birth_time:
        return None
    try:
        hh, mm = birth_time.split(":")
        hour = int(hh)
        minute = int(mm)
    except (ValueError, AttributeError):
        return None

    total_min = hour * 60 + minute
    if total_min >= 23 * 60 or total_min < 1 * 60:
        branch = "자"
    elif total_min < 3 * 60:
        branch = "축"
    elif total_min < 5 * 60:
        branch = "인"
    elif total_min < 7 * 60:
        branch = "묘"
    elif total_min < 9 * 60:
        branch = "진"
    elif total_min < 11 * 60:
        branch = "사"
    elif total_min < 13 * 60:
        branch = "오"
    elif total_min < 15 * 60:
        branch = "미"
    elif total_min < 17 * 60:
        branch = "신"
    elif total_min < 19 * 60:
        branch = "유"
    elif total_min < 21 * 60:
        branch = "술"
    else:
        branch = "해"

    ja_stem_table = {
        "갑": "갑", "기": "갑",
        "을": "병", "경": "병",
        "병": "무", "신": "무",
        "정": "경", "임": "경",
        "무": "임", "계": "임",
    }
    ja_stem = ja_stem_table[day_stem]
    ja_stem_idx = STEMS.index(ja_stem)
    branch_order_from_ja = ["자", "축", "인", "묘", "진", "사",
                            "오", "미", "신", "유", "술", "해"]
    offset = branch_order_from_ja.index(branch)
    stem = STEMS[(ja_stem_idx + offset) % 10]
    return stem, branch


@dataclass
class FourPillars:
    year: tuple[str, str]
    month: tuple[str, str]
    day: tuple[str, str]
    time: Optional[tuple[str, str]]


def _to_solar(
    birth_date: date,
    *,
    calendar_type: Literal["solar", "lunar"],
    is_leap_month: bool,
) -> date:
    if calendar_type == "solar":
        return birth_date
    cal = KoreanLunarCalendar()
    cal.setLunarDate(
        birth_date.year, birth_date.month, birth_date.day, is_leap_month
    )
    s = cal.SolarIsoFormat()
    return date.fromisoformat(s)


def calculate_four_pillars(
    birth_date: date,
    birth_time: Optional[str],
    *,
    calendar_type: Literal["solar", "lunar"] = "solar",
    is_leap_month: bool = False,
    birth_place: Optional[str] = None,
) -> FourPillars:
    adjusted_time = birth_time
    date_offset_days = 0
    if birth_time:
        original_total = _parse_minutes(birth_time)
        if original_total is not None:
            adjusted_time = _adjust_birth_time(birth_time, birth_place)
            adjusted_total = _parse_minutes(adjusted_time)
            if adjusted_total is not None and adjusted_total > original_total + 12 * 60:
                date_offset_days = -1
            elif adjusted_total is not None and adjusted_total + 12 * 60 < original_total:
                date_offset_days = 1

    solar = _to_solar(birth_date, calendar_type=calendar_type, is_leap_month=is_leap_month)
    if date_offset_days:
        solar = solar + timedelta(days=date_offset_days)

    year = _year_pillar(solar)
    month = _month_pillar(solar, year[0])
    day = _day_pillar(solar)
    time = _time_pillar(adjusted_time, day[0])

    return FourPillars(year=year, month=month, day=day, time=time)


def _parse_minutes(hhmm: str) -> Optional[int]:
    try:
        hh, mm = hhmm.split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def element_distribution_from_pillars(p: FourPillars) -> dict[str, int]:
    counts = {"wood": 0, "fire": 0, "earth": 0, "metal": 0, "water": 0}
    pillars: list[tuple[str, str]] = [p.year, p.month, p.day]
    if p.time is not None:
        pillars.append(p.time)
    for stem, branch in pillars:
        counts[STEM_ELEMENT[stem]] += 1
        counts[BRANCH_ELEMENT[branch]] += 1
    return counts