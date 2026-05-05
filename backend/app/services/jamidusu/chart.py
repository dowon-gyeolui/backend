"""자미두수 안성술 (安星術) — 결정론적 차트 계산.

표준 알고리즘 6단계 + 부성/사화:

  1. solar → lunar 변환 (KoreanLunarCalendar)
  2. 안명궁(安命宮) — 寅起正月 順月逆時
  3. 12궁 배치 — 명궁에서 역시계방향
  4. 안궁간(安宮干) — 五虎遁
  5. 정오행국(定五行局) — 명궁 60갑자 → 納音 → 局
  6. 안주성(安主星) — 紫微 → 14주성
  7. 안부성(安副星) — 6 副星 + 4 煞星 + 2 雜星
  8. 사화(四化) — 年干 → 化祿/化權/化科/化忌

공개 API: compute_chart()
"""

from __future__ import annotations

from datetime import date, time
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
    branch_idx,
    hour_to_branch_idx,
    stem_idx,
)
from app.services.jamidusu.schema import JamidusuChart, Palace, Star


def _solar_to_lunar(d: date) -> tuple[int, int, int, bool]:
    """양력 → (lunar_year, lunar_month, lunar_day, is_leap_month)."""
    cal = KoreanLunarCalendar()
    cal.setSolarDate(d.year, d.month, d.day)
    iso = cal.LunarIsoFormat()  # "YYYY-MM-DD" or "YYYY-MM-DD intercalation"
    parts = iso.split()
    ymd = parts[0]
    is_leap = "intercalation" in iso.lower() or "intercalation" in iso
    y, m, d2 = (int(x) for x in ymd.split("-"))
    return y, m, d2, is_leap


def _year_pillar_for(lunar_year: int) -> str:
    """입춘 무관 단순 60갑자. 1984=甲子 anchor.

    자미두수는 입춘 기준 사주와 달리 음력 정월 초하루를 해의 시작으로
    보는 게 일반적. 사주 엔진이 입춘 보정한 년주를 쓰면 자미두수 결과가
    어긋나니, 여기서는 음력 년 자체를 60갑자에 매핑.
    """
    # 1984 = 甲子(0). offset 으로 산출.
    offset = (lunar_year - 1984) % 60
    if offset < 0:
        offset += 60
    s = STEMS[offset % 10]
    b = BRANCHES[offset % 12]
    return f"{s}{b}"


def _ming_palace_branch_idx(lunar_month: int, hour_idx: int) -> int:
    """안명궁: 寅起正月 順 數至生月 → 逆 數至生時.

    Returns: branch_idx (0=子..11=亥).

    공식: ming_idx = (寅(2) + (월-1) - 시) mod 12
       = (1 + 월 - 시) mod 12
    """
    return (1 + lunar_month - hour_idx) % 12


def _body_palace_branch_idx(lunar_month: int, hour_idx: int) -> int:
    """안신궁: 寅起正月 順 數至生月 → 順 數至生時.

    공식: body_idx = (寅(2) + (월-1) + 시) mod 12 = (1 + 월 + 시) mod 12
    """
    return (1 + lunar_month + hour_idx) % 12


def _build_palaces(
    ming_idx: int, year_stem: str
) -> list[Palace]:
    """12궁 배치 + 五虎遁으로 천간 부여. stars 는 비워둠 (이후 채움)."""
    yin_stem = FIVE_TIGER[year_stem]   # 寅宮 천간
    yin_stem_idx = stem_idx(yin_stem)

    # 12궁: 명궁(0) ~ 부모궁(11) 을 명궁 인덱스에서 역시계로
    palaces: list[Palace] = []
    for i in range(12):
        pi = (ming_idx - i) % 12          # palace branch_idx
        # 寅(2) 기준으로 (pi - 2) % 12 만큼 cw 진행한 천간
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
    """紫微 위치 — 五行局 + 음력 생일."""
    table = ZIWEI_TABLE[bureau_num]
    # day 1..30 → index 0..29
    idx = max(0, min(29, lunar_day - 1))
    return table[idx]


def _add_star(palaces: list[Palace], branch_idx_: int, star: Star) -> None:
    """branch_idx 위치의 궁에 별을 추가."""
    for p in palaces:
        if p.branch_idx == branch_idx_:
            p.stars.append(star)
            return


def _place_main_stars(
    palaces: list[Palace], ziwei_idx: int
) -> None:
    """14 主星 안치."""
    # 紫微系 6
    for name, off in ZIWEI_GROUP_OFFSET:
        bi = (ziwei_idx + off) % 12
        _add_star(palaces, bi, Star(name=name, name_ko=STAR_KO_NAME[name], type="main"))
    # 天府系 8
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
    """副星·煞星·雜星 안치."""
    # 左輔 = 辰(4) + (月-1) cw
    li = (4 + (lunar_month - 1)) % 12
    _add_star(palaces, li, Star("左輔", STAR_KO_NAME["左輔"], "lucky"))
    # 右弼 = 戌(10) - (月-1) ccw = 戌(10) + 1 - 월
    ri = (10 - (lunar_month - 1)) % 12
    _add_star(palaces, ri, Star("右弼", STAR_KO_NAME["右弼"], "lucky"))
    # 文昌 = 戌(10) - 시 ccw
    cs = (10 - hour_idx) % 12
    _add_star(palaces, cs, Star("文昌", STAR_KO_NAME["文昌"], "lucky"))
    # 文曲 = 辰(4) + 시 cw
    cq = (4 + hour_idx) % 12
    _add_star(palaces, cq, Star("文曲", STAR_KO_NAME["文曲"], "lucky"))
    # 天魁/天鉞
    kui_idx, yue_idx = KUI_YUE_BY_STEM[year_stem]
    _add_star(palaces, kui_idx, Star("天魁", STAR_KO_NAME["天魁"], "lucky"))
    _add_star(palaces, yue_idx, Star("天鉞", STAR_KO_NAME["天鉞"], "lucky"))
    # 祿存
    lc = LU_CUN_BY_STEM[year_stem]
    _add_star(palaces, lc, Star("祿存", STAR_KO_NAME["祿存"], "lucky"))
    # 擎羊 = 祿存 + 1, 陀羅 = 祿存 - 1
    _add_star(palaces, (lc + 1) % 12, Star("擎羊", STAR_KO_NAME["擎羊"], "unlucky"))
    _add_star(palaces, (lc - 1) % 12, Star("陀羅", STAR_KO_NAME["陀羅"], "unlucky"))
    # 天馬
    tm = TIANMA_BY_BRANCH[year_branch]
    _add_star(palaces, tm, Star("天馬", STAR_KO_NAME["天馬"], "lucky"))
    # 火星 / 鈴星 = 三合 시작점 + 시
    huo_start, ling_start = HUO_LING_START_BY_BRANCH[year_branch]
    _add_star(palaces, (huo_start + hour_idx) % 12, Star("火星", STAR_KO_NAME["火星"], "unlucky"))
    _add_star(palaces, (ling_start + hour_idx) % 12, Star("鈴星", STAR_KO_NAME["鈴星"], "unlucky"))


def _apply_sihwa(palaces: list[Palace], year_stem: str) -> None:
    """年干 기준 사화(化祿/化權/化科/化忌) 를 해당 별이 있는 궁에 marker 추가.

    사화는 별이 아니라 '본주성에 붙는 라벨' 이라, 같은 별이 동궁에 없으면
    효과가 없을 수도 있음. 본주성을 찾아서 sub 라벨 부여.
    """
    mapping = SIHWA_BY_STEM[year_stem]
    # mapping: {"化祿": "廉貞", ...}
    for sihwa_label, target_star_name in mapping.items():
        # palaces 에서 그 별을 찾아서 sub label 추가 (별도 Star 로 추가)
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
                    break  # 같은 별이 두 궁에 있을 일은 없으니 첫 매치에서 종료
            else:
                continue
            break


# ── public API ────────────────────────────────────────────────────


def compute_chart(
    birth_date: date,
    *,
    birth_time: Optional[str] = None,
    calendar_type: str = "solar",
    is_leap_month: bool = False,
    gender: Optional[str] = None,
) -> JamidusuChart:
    """Build a deterministic 자미두수 chart.

    Parameters
    ----------
    birth_date : date
        Solar or lunar (per `calendar_type`). For lunar, no leap-month
        handling here — caller passes `is_leap_month=True` if needed.
    birth_time : "HH:MM" or None
        시간 모름이면 자시(子時, 0시) 가정. `hour_assumed=True` 로 표시.
    calendar_type : "solar" | "lunar"
    is_leap_month : bool
    gender : "male" | "female" | None

    Returns
    -------
    JamidusuChart
    """
    # 1. solar → lunar
    if calendar_type == "lunar":
        lunar_y, lunar_m, lunar_d = birth_date.year, birth_date.month, birth_date.day
        is_leap = bool(is_leap_month)
    else:
        lunar_y, lunar_m, lunar_d, is_leap = _solar_to_lunar(birth_date)

    # 2. 시진 결정 (모름 → 子時 가정)
    hour_assumed = birth_time is None
    if birth_time:
        try:
            hh = int(birth_time.split(":", 1)[0])
        except (ValueError, IndexError):
            hh = 0
            hour_assumed = True
    else:
        hh = 0  # 子時 (00시 가정)
    hour_idx = hour_to_branch_idx(hh)

    # 3. 년주 60갑자 (입춘 보정 없음 — 자미두수 표준)
    year_pillar = _year_pillar_for(lunar_y)
    year_stem = year_pillar[0]
    year_branch = year_pillar[1]

    # 4. 안명궁 / 안신궁
    ming_idx = _ming_palace_branch_idx(lunar_m, hour_idx)
    body_idx = _body_palace_branch_idx(lunar_m, hour_idx)

    # 5. 12궁 배치 + 五虎遁
    palaces = _build_palaces(ming_idx, year_stem)

    # 6. 五行局 — 명궁 60갑자(천간+지지) 로 룩업
    ming_palace = palaces[0]  # palaces[0] 이 명궁
    ming_pillar = f"{ming_palace.stem}{ming_palace.branch}"
    bureau_name, bureau_num = NAYIN_BUREAU[ming_pillar]

    # 7. 紫微 위치
    ziwei_idx = _ziwei_position(lunar_d, bureau_num)

    # 8. 14 主星 안치
    _place_main_stars(palaces, ziwei_idx)

    # 9. 副星 (左輔 右弼 文昌 文曲 天魁 天鉞 祿存 擎羊 陀羅 天馬 火星 鈴星)
    _place_secondary(palaces, lunar_m, hour_idx, year_stem, year_branch)

    # 10. 사화
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