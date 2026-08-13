"""사주 엔진 스냅샷 — 로직이 조용히 바뀌면 여기서 깨진다.

이건 "정답 검증"이 아니라 **특성화(characterization) 테스트**다.
2026-08-13 시점 엔진 출력을 고정해 두고, 이후 변경이 의도치 않게 결과를 바꾸는 것을 잡는다.
정통 명리 기준으로 값이 틀렸다고 판단되면 명시적으로 기대값을 고치고 그 이유를 커밋에 남길 것.
"""

from datetime import date

import pytest

from app.services.saju_engine import calculate_four_pillars

# (생년월일, 출생시각) → (년주, 월주, 일주, 시주)
SNAPSHOTS = [
    (
        date(1990, 5, 15),
        "14:30",
        (("경", "오"), ("신", "사"), ("경", "진"), ("계", "미")),
    ),
    (
        date(2000, 1, 1),
        "00:00",
        (("기", "묘"), ("병", "자"), ("정", "사"), ("경", "자")),
    ),
    (
        date(1985, 11, 3),
        None,
        (("을", "축"), ("병", "술"), ("병", "오"), None),
    ),
    (
        date(1996, 2, 29),  # 윤일
        "23:45",
        (("병", "자"), ("경", "인"), ("병", "신"), ("무", "자")),
    ),
]


@pytest.mark.parametrize("birth_date,birth_time,expected", SNAPSHOTS)
def test_four_pillars_snapshot(birth_date, birth_time, expected):
    p = calculate_four_pillars(birth_date, birth_time)
    assert (p.year, p.month, p.day, p.time) == expected


def test_unknown_birth_time_yields_no_time_pillar():
    """OI-SAJU-001 확정: 출생시간 미상은 생년월일만으로 처리한다."""
    p = calculate_four_pillars(date(1992, 7, 7), None)
    assert p.time is None
    assert p.year and p.month and p.day


def test_engine_is_deterministic():
    a = calculate_four_pillars(date(1993, 9, 9), "09:09")
    b = calculate_four_pillars(date(1993, 9, 9), "09:09")
    assert (a.year, a.month, a.day, a.time) == (b.year, b.month, b.day, b.time)


def test_lunar_and_solar_differ():
    solar = calculate_four_pillars(date(1990, 5, 15), None, calendar_type="solar")
    lunar = calculate_four_pillars(date(1990, 5, 15), None, calendar_type="lunar")
    assert solar.day != lunar.day
