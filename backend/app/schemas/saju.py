from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel


class BirthInputSummary(BaseModel):
    """Echoes the birth data that was used as calculation input."""

    birth_date: date
    birth_time: Optional[str] = None   # "HH:MM" or None if unknown
    calendar_type: str = "solar"       # "solar" | "lunar"
    is_leap_month: bool = False
    gender: Optional[str] = None


class Pillar(BaseModel):
    """One of the four saju pillars (년주/월주/일주/시주)."""

    label: str    # "년주" | "월주" | "일주" | "시주"
    stem: str     # 천간 (heavenly stem), e.g. "갑"
    branch: str   # 지지 (earthly branch), e.g. "자"
    combined: str  # stem + branch, e.g. "갑자"

    # 명식 chart fields — populated alongside `stem`/`branch`. Optional so
    # legacy callers (the rule-based compatibility scorer, mostly) keep
    # working without reading them.
    stem_hanja: Optional[str] = None
    stem_element: Optional[str] = None      # "wood"/"fire"/"earth"/"metal"/"water"
    stem_polarity: Optional[str] = None     # "+" | "-"
    stem_ten_god: Optional[str] = None
    branch_hanja: Optional[str] = None
    branch_animal: Optional[str] = None
    branch_element: Optional[str] = None
    branch_polarity: Optional[str] = None
    branch_ten_god: Optional[str] = None
    hidden_stems: list[str] = []
    twelve_stage: Optional[str] = None
    twelve_spirit: Optional[str] = None


class ElementProfile(BaseModel):
    """오행 (five elements) count derived from the four pillars' heavenly stems.

    TODO: Include earthly branches in real calculation for a full 8-character reading.
    """

    wood: int = 0   # 목(木)
    fire: int = 0   # 화(火)
    earth: int = 0  # 토(土)
    metal: int = 0  # 금(金)
    water: int = 0  # 수(水)


class SajuResponse(BaseModel):
    user_id: int
    input_summary: BirthInputSummary
    pillars: list[Pillar]          # [년주, 월주, 일주, 시주]
    element_profile: ElementProfile
    summary: str                   # Short Korean provisional summary

    # --- Retrieval-grounded interpretation layer ---
    # Pipeline:
    #   retrieved chunks (sources) → LLM summarization → interpretation
    #
    # interpretation_status semantics:
    #   "pending" — retrieval produced nothing relevant OR embedding unavailable
    #   "ready"   — retrieval returned at least one vector-similarity match
    #
    # `interpretation_sources` is the citation list (always populated when ready).
    # `interpretation` is the LLM-generated Korean summary, grounded strictly
    # in those sources. It may be null even when status="ready" if the LLM
    # call failed or was skipped — UI should gracefully fall back to showing
    # the citations alone.
    interpretation_status: Literal["pending", "ready"] = "pending"
    interpretation_sources: list[str] = []
    interpretation: Optional[str] = None


class DetailedSajuResponse(SajuResponse):
    """SajuResponse + 4-section LLM interpretation (성격/연애/재물/조언).

    Each section is a 2-3 sentence Korean interpretation grounded in the
    same RAG passages used for `interpretation`. Sections may be empty
    strings when the LLM failed for that category specifically; the
    frontend should render a graceful placeholder for empties.

    Health was intentionally removed — fortune-telling shouldn't make
    medical claims, and we don't want the user to act on them.
    """

    personality: str = ""
    love: str = ""
    wealth: str = ""
    advice: str = ""


class TodayFortuneResponse(BaseModel):
    """오늘의 인연운 — 사용자 사주 + 오늘 일진 기반 일일 fortune (multi-section).

    fortune_text 는 메인 한 줄. 나머지 필드는 카드의 세부 칸:
      - person_type: 만나는 사람 성향
      - timing: 만남 좋은 시간대
      - place: 만남 좋은 장소 분위기
      - caution: 주의사항
      - lucky_color: 행운 색상
      - badges: ["도화 발동", "삼합 길일", "천을귀인 길일"] 같은 강조 칩
    """

    fortune_text: str
    today_pillar: str
    today_pillar_hanja: str
    relation: str
    element_today: str
    score: int
    headline: str = ""
    person_type: str = ""
    timing: str = ""
    place: str = ""
    caution: str = ""
    lucky_color: str = ""
    badges: list[str] = []


class ActionGuideResponse(BaseModel):
    """오늘의 행동 가이드 — 사주 기반 3줄 산문 (반말 톤).

    옷차림 / 태도 / 마음가짐 세 가지 관점이 자연스럽게 녹아있는
    3줄 글. 클라이언트는 text 만 그대로 표시.
    """

    text: str


class JamidusuPalace(BaseModel):
    """One of the 12 자미두수 palaces with its LLM-generated reading."""

    name: str         # e.g. "命宮 (명궁)"
    description: str  # one-line reading, 30~80자


class JamidusuResponse(BaseModel):
    """자미두수 (Zǐwēi Dòushù) interpretation for the premium drawer.

    Anchored on the user's saju (we don't compute a real 자미두수 chart for
    MVP — the LLM bridges between the two systems given saju context).

    `palaces` covers the canonical 12 궁; `main_stars_summary` describes
    where the major 14주성 cluster falls; `overview` is the closing
    paragraph the UI shows at the top.
    """

    user_id: int
    overview: str = ""
    palaces: list[JamidusuPalace] = []
    main_stars_summary: str = ""
    interpretation_status: Literal["pending", "ready"] = "pending"


# ─── 자미두수 Deep (사주 + 자미두수 융합) ─────────────────────────


class JamidusuDeepStar(BaseModel):
    """차트 계산 결과의 별 한 개."""

    name: str          # 한자명 — "紫微"
    name_ko: str       # 한글 — "황제의 별"
    type: str          # "main" | "lucky" | "unlucky" | "transform"
    sub: Optional[str] = None   # 사화 라벨이 붙은 본주성 이름


class JamidusuDeepPalace(BaseModel):
    """12궁 한 개 — 차트 계산 결과 + LLM 풀이."""

    name: str            # 한자명 — "命宮"
    name_ko: str         # 한글 — "명궁"
    branch: str          # "申"
    branch_ko: str       # "신"
    stem: str            # "甲"
    stem_ko: str         # "갑"
    stars: list[JamidusuDeepStar] = []
    description: str = ""
    """LLM 풀이 — 사주 일간 영향 곁들인 연애 관점 풀이 2~3 문장."""


class JamidusuDeepSections(BaseModel):
    """4 섹션 LLM 풀이 (사주 + 자미두수 융합 관점)."""

    personality: str = ""   # 연애할 때 모습·매력
    love: str = ""          # 이상형·끌리는 인연
    wealth: str = ""        # 데이트 자금 감각
    advice: str = ""        # 좋은 인연 만나기 위한 제안


class JamidusuDeepResponse(BaseModel):
    """결정론 차트 + RAG-grounded LLM 풀이를 융합한 deep 응답.

    Pipeline:
      1. compute_chart() — 12궁×별 결정론 계산
      2. retrieve() — 자미두수전서·궁통보감 RAG passages
      3. LLM — 차트 + 사주 + 원전 → JSON 풀이
    """

    user_id: int
    interpretation_status: Literal["pending", "ready", "partial"] = "pending"

    # 차트 메타
    bureau_name: str = ""        # 五行局 — "水二局"
    year_pillar: str = ""        # 60갑자 — "乙亥"
    lunar_birth: Optional[str] = None  # "1995-02-15(음)"
    hour_assumed: bool = False   # 시간 모름 → 子時 가정

    # LLM 결과
    headline: str = ""
    overview: str = ""
    sections: JamidusuDeepSections = JamidusuDeepSections()
    palaces: list[JamidusuDeepPalace] = []
    main_stars_summary: str = ""

    # 원전 출처
    sources: list[str] = []
