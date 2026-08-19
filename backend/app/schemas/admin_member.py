"""관리자 회원 화면 스키마 (ADM-MEM-001/002).

**민감정보 필드는 전부 `str` 이다.** 마스킹된 값("1995-**-**")과 원문("1995-03-20")이
같은 필드로 오가야 화면이 두 벌이 되지 않는다. `date` 로 두면 마스킹된 값이 형식
오류가 되어, 마스킹을 우회하는 별도 응답 모델을 만들게 된다.

기본 응답은 **항상 마스킹된 값**이다. 원문은 사유를 받는 별도 엔드포인트로만 나가고
(OI-MEM-004), 응답에 담길 뿐 서버는 "이 관리자는 원문을 본다"는 상태를 남기지 않는다.
그래서 새로고침하면 다시 마스킹된다 — 화면이 잊어버리는 것이 아니라 서버가 매번
마스킹해서 준다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.user import STATUS_ACTIVE, STATUS_BLOCKED, STATUS_INACTIVE

MemberStatus = Literal[STATUS_ACTIVE, STATUS_INACTIVE, STATUS_BLOCKED]

# 변경 사유는 감사 로그의 핵심이다(B-2: "누가 왜 정지했나"). 빈 문자열이나 "."
# 한 글자로 통과하면 로그가 남아도 쓸모가 없어 최소 길이를 둔다.
_MIN_REASON = 2
_MAX_REASON = 200


class MemberListItem(BaseModel):
    """목록 한 행. 동명이인을 가르는 것은 `id` 와 `created_at` 이다."""

    id: int
    nickname: str | None
    gender: str | None
    region: str | None
    status: MemberStatus
    profile_hidden: bool
    matchable: bool
    matchable_blockers: list[str]
    kakao_id: str | None
    username: str | None
    birth_date: str | None
    star_balance: int
    is_paid: bool
    created_at: datetime


class MemberListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[MemberListItem]


class MemberBasicInfo(BaseModel):
    """기본정보 탭. 마스킹 대상이 모여 있는 유일한 곳이다."""

    kakao_id: str | None
    username: str | None
    birth_date: str | None
    birth_time: str | None
    birth_place: str | None
    calendar_type: str | None
    is_leap_month: bool
    gender: str | None


class MemberProfile(BaseModel):
    nickname: str | None
    bio: str | None
    photo_url: str | None
    photo_count: int
    height_cm: int | None
    mbti: str | None
    job: str | None
    region: str | None
    smoking: str | None
    drinking: str | None
    religion: str | None
    pref_age_min: int | None
    pref_age_max: int | None
    pref_region: str | None
    pref_height_min: int | None


class MemberPillar(BaseModel):
    label: str
    combined: str


class MemberSaju(BaseModel):
    """사주 요약. 기존 엔진(`services/saju.calculate`) 결과를 줄여 담는다.

    생년월일 원문은 담지 않는다 — 기본정보 탭의 마스킹을 이 탭이 뒤집으면 안 된다.
    """

    pillars: list[MemberPillar]
    elements: dict[str, int]
    summary: str


class MemberCardUnlock(BaseModel):
    candidate_id: int
    kind: str
    unlocked_at: datetime


class MemberMatching(BaseModel):
    daily_count: int
    extra_count: int
    blocking_count: int
    blocked_by_count: int
    strike_count: int
    recent_unlocks: list[MemberCardUnlock]


class MemberOrder(BaseModel):
    order_id: str
    product_id: str
    amount: int
    star_amount: int
    status: str
    created_at: datetime
    paid_at: datetime | None


class MemberLedgerEntry(BaseModel):
    entry_type: str
    reference_id: str
    amount: int
    balance_after: int
    created_at: datetime


class MemberBilling(BaseModel):
    star_balance: int
    is_paid: bool
    orders: list[MemberOrder]
    ledger: list[MemberLedgerEntry]


class MemberSummary(BaseModel):
    id: int
    nickname: str | None
    status: MemberStatus
    profile_hidden: bool
    matchable: bool
    matchable_blockers: list[str]
    chat_suspended_until: datetime | None
    created_at: datetime
    updated_at: datetime


class MemberDetail(BaseModel):
    """상세 화면의 탭 다섯 개를 한 번에 내린다.

    탭마다 요청을 나누지 않는 것은 회원 한 명의 자료가 작고, 탭을 옮길 때마다 로딩이
    끼면 "동명이인 두 명을 나란히 비교"하는 실제 사용 흐름이 끊기기 때문이다.
    """

    summary: MemberSummary
    basic: MemberBasicInfo
    profile: MemberProfile
    saju: MemberSaju | None
    matching: MemberMatching
    billing: MemberBilling


class MemberStatusUpdate(BaseModel):
    status: MemberStatus
    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)


class MemberVisibilityUpdate(BaseModel):
    hidden: bool
    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)


class MemberUnmaskRequest(BaseModel):
    """원문 열람 사유. 선택이 아니라 필수다(OI-MEM-004)."""

    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)


class MemberUnmaskResponse(BaseModel):
    """원문. 이 응답만 마스킹을 거치지 않는다."""

    id: int
    kakao_id: str | None
    username: str | None
    birth_date: str | None
    birth_time: str | None
    birth_place: str | None
