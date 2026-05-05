"""자미두수 차트 dataclass — 계산 결과를 들고 다닐 내부 자료구조.

API 응답용 Pydantic 모델은 `app.schemas.saju` 에 별도. 이 모듈은
계산 단계에서만 쓰는 가벼운 dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

StarType = Literal["main", "lucky", "unlucky", "transform"]
"""주성/길성/흉성/사화 분류."""


@dataclass
class Star:
    """자미두수 별 한 개."""

    name: str            # 한자명 — 紫微 / 太陽 / 文昌 / ...
    name_ko: str         # 한글 별칭 — 황제의 별 / 태양의 별 / 글의 별
    type: StarType       # main(14주성) / lucky / unlucky / transform(사화)
    sub: Optional[str] = None
    """사화 라벨 ('化祿'/'化權'/'化科'/'化忌'). 사화 별에만 존재."""


@dataclass
class Palace:
    """12궁 한 개. 명궁부터 부모궁까지."""

    name: str            # 한자명 — 命宮 / 兄弟宮 / ...
    name_ko: str         # 한글 — 명궁 / 형제궁 / ...
    branch: str          # 12지지 한자 — 子 / 丑 / ...
    branch_ko: str       # 12지지 한글 — 자 / 축 / ...
    branch_idx: int      # 0..11
    stem: str            # 12궁 천간 한자 — 五虎遁 결과
    stem_ko: str         # 한글
    stars: list[Star] = field(default_factory=list)


@dataclass
class JamidusuChart:
    """완성된 자미두수 차트."""

    # 입력 메타
    lunar_year: int
    lunar_month: int
    lunar_day: int
    is_leap_month: bool
    birth_hour: Optional[int]   # 0-23 KST. None 이면 子時 가정 + 정확도 ↓
    hour_assumed: bool          # birth_time 모름 시 True
    gender: Optional[str]
    year_pillar: str            # 60갑자 — 五虎遁/사화 인풋

    # 계산 결과
    bureau_name: str            # 五行局 한자명 — "水二局"
    bureau_num: int             # 局數 — 2,3,4,5,6
    ming_branch_idx: int        # 명궁의 12지지 인덱스
    body_branch_idx: int        # 신궁의 12지지 인덱스
    ziwei_branch_idx: int       # 紫微 위치
    palaces: list[Palace] = field(default_factory=list)
    """12개. 인덱스 0=명궁..11=부모궁."""

    def palace_by_name(self, name_ko: str) -> Optional[Palace]:
        """한글 궁명으로 찾기."""
        for p in self.palaces:
            if p.name_ko == name_ko:
                return p
        return None