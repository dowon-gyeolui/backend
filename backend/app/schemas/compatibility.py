from typing import Optional

from pydantic import BaseModel


class CompatibilityScore(BaseModel):
    """Pairwise compatibility score between two users."""

    user_a_id: int
    user_b_id: int
    score: int  # 0~100
    # Short Korean summary built from pillar comparison + element balance.
    summary: Optional[str] = None


class CompatibilityReport(BaseModel):
    """Drawer-style 운명 분석 리포트 for the chat header.

    Two narrative bullets (synergy + caution) + 3 hashtag keywords feed the
    Figma drawer at node 37:1657. CTA gating is a frontend concern.
    """

    user_a_id: int
    user_b_id: int
    nickname_a: Optional[str] = None
    nickname_b: Optional[str] = None
    score: int  # 0~100

    # First line is the synergy/strength; second is the caution/risk. Two
    # bullets line up with Figma's two ✦-prefixed paragraphs.
    summary_lines: list[str]

    # Three hashtag-style chips (e.g. "#금의_기운", "#찰떡궁합", "#솔직한_대화")
    keywords: list[str]


class MatchCandidate(BaseModel):
    """One match candidate shown as a profile card.

    Fields always visible:
      user_id, score, nickname, age, gender, is_blinded

    Free tier (is_blinded=True):
      photo_url = None            — 사진 블라인드(모자이크 대체)
      birth_year = None           — 정확한 연도 비공개
      dominant_element = None

    Paid tier (is_blinded=False):
      photo_url                   — 본 사진 공개
      birth_year                  — 생년 노출
      dominant_element            — 주요 오행 공개로 대화 맥락 제공
    """

    user_id: int
    score: int  # 0~100
    nickname: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    is_blinded: bool = True

    # Unblinded-only extras (null when is_blinded=True)
    photo_url: Optional[str] = None
    birth_year: Optional[int] = None
    dominant_element: Optional[str] = None
    mbti: Optional[str] = None
