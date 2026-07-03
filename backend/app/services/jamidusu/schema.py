"""자미두수 차트 계산 중간 산출물용 내부 dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

StarType = Literal["main", "lucky", "unlucky", "transform"]


@dataclass
class Star:
    name: str
    name_ko: str
    type: StarType
    sub: Optional[str] = None


@dataclass
class Palace:
    name: str
    name_ko: str
    branch: str
    branch_ko: str
    branch_idx: int
    stem: str
    stem_ko: str
    stars: list[Star] = field(default_factory=list)


@dataclass
class JamidusuChart:
    lunar_year: int
    lunar_month: int
    lunar_day: int
    is_leap_month: bool
    birth_hour: Optional[int]
    hour_assumed: bool
    gender: Optional[str]
    year_pillar: str

    bureau_name: str
    bureau_num: int
    ming_branch_idx: int
    body_branch_idx: int
    ziwei_branch_idx: int
    palaces: list[Palace] = field(default_factory=list)

    def palace_by_name(self, name_ko: str) -> Optional[Palace]:
        for p in self.palaces:
            if p.name_ko == name_ko:
                return p
        return None