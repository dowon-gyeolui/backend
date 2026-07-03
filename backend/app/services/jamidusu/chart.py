"""자미두수 안성술(安星術) 차트 계산 본체."""

from __future__ import annotations

from datetime import date
from typing import Optional

from korean_lunar_calendar import KoreanLunarCalendar

from app.services.jamidusu.constants import (
    BRANCH_KO,
    BRANCHES,
    FIVE_TIGER,
    HUO_LING_START_BY_BRANCH,
    KUI_YUE_BY_STEM,
    LU_CUN_BY_STEM,
    NAYIN_BUREAU,
    PALACE_NAMES,
    PALACE_NAMES_KO,
    SIHWA_BY_STEM,
    STAR_KO_NAME,
    STEM_KO,
    STEMS,
    TIANFU_GROUP_OFFSET,
    TIANMA_BY_BRANCH,
    ZIWEI_GROUP_OFFSET,
    ZIWEI_TABLE,
    ZIWEI_TIANFU_TABLE,
    hour_to_branch_idx,
    stem_idx,
)
from app.services.jamidusu.schema import JamidusuChart, Palace, Star


def _solar_to_lunar(d: date) -> tuple[int, int, int, bool]:
    cal = KoreanLunarCalendar()
    cal.setSolarDate(d.year, d.month, d.day)
    iso = cal.LunarIsoFormat()
    parts = iso.split()
    ymd = parts[0]
    is_leap = "intercalation" in iso.lower() or "intercalation" in iso
    y, m, d2 = (int(x) for x in ymd.split("-"))
    return y, m, d2, is_leap


def _year_pillar_for(lunar_year: int) -> str:
    offset = (lunar_year - 1984) % 60
    if offset < 0:
        offset += 60
    s = STEMS[offset % 10]
    b = BRANCHES[offset % 12]
    return f"{s}{b}"


def _ming_palace_branch_idx(lunar_month: int, hour_idx: int) -> int:
    return (1 + lunar_month - hour_idx) % 12


def _body_palace_branch_idx(lunar_month: int, hour_idx: int) -> int:
    return (1 + lunar_month + hour_idx) % 12


def _build_palaces(
    ming_idx: int, year_stem: str
) -> list[Palace]:
    yin_stem = FIVE_TIGER[year_stem]
    yin_stem_idx = stem_idx(yin_stem)

    palaces: list[Palace] = []
    for i in range(12):
        pi = (ming_idx - i) % 12
        steps_from_yin = (pi - 2) % 12
        si = (yin_stem_idx + steps_from_yin) % 10
        stem_h = STEMS[si]
        branch_h = BRANCHES[pi]
        palaces.append(
            Palace(
                name=PALACE_NAMES[i],
                name_ko=PALACE_NAMES_KO[i],
                branch=branch_h,
                branch_ko=BRANCH_KO[branch_h],
                branch_idx=pi,
                stem=stem_h,
                stem_ko=STEM_KO[stem_h],
            )
        )
    return palaces


def _ziwei_position(lunar_day: int, bureau_num: int) -> int:
    table = ZIWEI_TABLE[bureau_num]
    idx = max(0, min(29, lunar_day - 1))
    return table[idx]


def _add_star(palaces: list[Palace], branch_idx_: int, star: Star) -> None:
    for p in palaces:
        if p.branch_idx == branch_idx_:
            p.stars.append(star)
            return


def _place_main_stars(
    palaces: list[Palace], ziwei_idx: int
) -> None:
    for name, off in ZIWEI_GROUP_OFFSET:
        bi = (ziwei_idx + off) % 12
        _add_star(palaces, bi, Star(name=name, name_ko=STAR_KO_NAME[name], type="main"))
    tianfu_idx = ZIWEI_TIANFU_TABLE[ziwei_idx]
    for name, off in TIANFU_GROUP_OFFSET:
        bi = (tianfu_idx + off) % 12
        _add_star(palaces, bi, Star(name=name, name_ko=STAR_KO_NAME[name], type="main"))


def _place_secondary(
    palaces: list[Palace],
    lunar_month: int,
    hour_idx: int,
    year_stem: str,
    year_branch: str,
) -> None:
    li = (4 + (lunar_month - 1)) % 12
    _add_star(palaces, li, Star("左輔", STAR_KO_NAME["左輔"], "lucky"))
    ri = (10 - (lunar_month - 1)) % 12
    _add_star(palaces, ri, Star("右弼", STAR_KO_NAME["右弼"], "lucky"))
    cs = (10 - hour_idx) % 12
    _add_star(palaces, cs, Star("文昌", STAR_KO_NAME["文昌"], "lucky"))
    cq = (4 + hour_idx) % 12
    _add_star(palaces, cq, Star("文曲", STAR_KO_NAME["文曲"], "lucky"))
    kui_idx, yue_idx = KUI_YUE_BY_STEM[year_stem]
    _add_star(palaces, kui_idx, Star("天魁", STAR_KO_NAME["天魁"], "lucky"))
    _add_star(palaces, yue_idx, Star("天鉞", STAR_KO_NAME["天鉞"], "lucky"))
    lc = LU_CUN_BY_STEM[year_stem]
    _add_star(palaces, lc, Star("祿存", STAR_KO_NAME["祿存"], "lucky"))
    _add_star(palaces, (lc + 1) % 12, Star("擎羊", STAR_KO_NAME["擎羊"], "unlucky"))
    _add_star(palaces, (lc - 1) % 12, Star("陀羅", STAR_KO_NAME["陀羅"], "unlucky"))
    tm = TIANMA_BY_BRANCH[year_branch]
    _add_star(palaces, tm, Star("天馬", STAR_KO_NAME["天馬"], "lucky"))
    huo_start, ling_start = HUO_LING_START_BY_BRANCH[year_branch]
    _add_star(palaces, (huo_start + hour_idx) % 12, Star("火星", STAR_KO_NAME["火星"], "unlucky"))
    _add_star(palaces, (ling_start + hour_idx) % 12, Star("鈴星", STAR_KO_NAME["鈴星"], "unlucky"))


def _apply_sihwa(palaces: list[Palace], year_stem: str) -> None:
    mapping = SIHWA_BY_STEM[year_stem]
    for sihwa_label, target_star_name in mapping.items():
        for p in palaces:
            for s in p.stars:
                if s.name == target_star_name:
                    p.stars.append(
                        Star(
                            name=sihwa_label,
                            name_ko=STAR_KO_NAME[sihwa_label],
                            type="transform",
                            sub=target_star_name,
                        )
                    )
                    break
            else:
                continue
            break


def compute_chart(
    birth_date: date,
    *,
    birth_time: Optional[str] = None,
    calendar_type: str = "solar",
    is_leap_month: bool = False,
    gender: Optional[str] = None,
) -> JamidusuChart:
    if calendar_type == "lunar":
        lunar_y, lunar_m, lunar_d = birth_date.year, birth_date.month, birth_date.day
        is_leap = bool(is_leap_month)
    else:
        lunar_y, lunar_m, lunar_d, is_leap = _solar_to_lunar(birth_date)

    hour_assumed = birth_time is None
    if birth_time:
        try:
            hh = int(birth_time.split(":", 1)[0])
        except (ValueError, IndexError):
            hh = 0
            hour_assumed = True
    else:
        hh = 0
    hour_idx = hour_to_branch_idx(hh)

    year_pillar = _year_pillar_for(lunar_y)
    year_stem = year_pillar[0]
    year_branch = year_pillar[1]

    ming_idx = _ming_palace_branch_idx(lunar_m, hour_idx)
    body_idx = _body_palace_branch_idx(lunar_m, hour_idx)

    palaces = _build_palaces(ming_idx, year_stem)

    ming_palace = palaces[0]
    ming_pillar = f"{ming_palace.stem}{ming_palace.branch}"
    bureau_name, bureau_num = NAYIN_BUREAU[ming_pillar]

    ziwei_idx = _ziwei_position(lunar_d, bureau_num)

    _place_main_stars(palaces, ziwei_idx)

    _place_secondary(palaces, lunar_m, hour_idx, year_stem, year_branch)

    _apply_sihwa(palaces, year_stem)

    return JamidusuChart(
        lunar_year=lunar_y,
        lunar_month=lunar_m,
        lunar_day=lunar_d,
        is_leap_month=is_leap,
        birth_hour=hh if not hour_assumed else None,
        hour_assumed=hour_assumed,
        gender=gender,
        year_pillar=year_pillar,
        bureau_name=bureau_name,
        bureau_num=bureau_num,
        ming_branch_idx=ming_idx,
        body_branch_idx=body_idx,
        ziwei_branch_idx=ziwei_idx,
        palaces=palaces,
    )