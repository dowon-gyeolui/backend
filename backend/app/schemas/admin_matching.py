"""관리자 매칭 대상자·후보 검증 스키마 (ADM-MATCH-001/002).

이 화면의 응답은 **판단이 아니라 근거**를 싣는다. "이 후보는 적합하다"가 아니라
"어떤 조건을 어떤 값으로 통과했는가", "어느 조건에서 처음 탈락했는가"를 그대로
내려 보내고, 문장으로 요약하는 일은 화면에 맡긴다. 요약된 결과만 내려보내면
운영자가 "왜 이 사람이 빠졌는지"를 확인할 방법이 서버 로그밖에 남지 않는다.

민감정보는 담지 않는다. 후보 목록에는 닉네임·나이·지역처럼 앱에서 상대에게
그대로 보이는 값만 있고, 생년월일·연락처는 회원 상세(ADM-MEM-002)에서 사유를
남겨야 볼 수 있다. 매칭 검증을 우회 열람 통로로 만들지 않는다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.user import STATUS_ACTIVE, STATUS_BLOCKED, STATUS_INACTIVE

MemberStatus = Literal[STATUS_ACTIVE, STATUS_INACTIVE, STATUS_BLOCKED]

# 대상자 한 줄의 상태. 목록은 이 순서대로 정렬된다 — 문제가 있는 대상자를 찾으려고
# 페이지를 넘겨야 한다면 목록이 제 역할을 못 하는 것이다.
TargetState = Literal["error", "no_candidate", "ok"]

_MIN_REASON = 2
_MAX_REASON = 200


class FilterCode(BaseModel):
    """조건 하나의 코드와 설명. 화면의 범례이자 제외 사유의 사전이다."""

    code: str
    label: str
    description: str


class TargetListItem(BaseModel):
    id: int
    nickname: str | None
    gender: str | None
    status: MemberStatus
    profile_hidden: bool
    created_at: datetime
    state: TargetState
    #: 대상자 **자신** 때문에 추천이 성립하지 않는 사유(성별 미입력 등).
    issues: list[str]
    #: 하드필터를 통과한 후보 수.
    pool_count: int
    #: 그중 대상자의 선호 조건(완화 전)까지 만족하는 후보 수.
    preferred_count: int


class TargetListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[TargetListItem]


class ConditionSnapshot(BaseModel):
    """검증 시점의 조건. 나중에 프로필이 바뀌면 이 값도 바뀐다.

    발급된 카드의 **당시** 스냅샷은 여기가 아니라 추천 이력(T-E05)이 다룬다.
    이 화면은 "지금 다시 계산하면 무엇이 나오는가"를 본다.
    """

    evaluated_at: datetime
    allow_same_gender: bool
    #: 후보가 될 수 있는 성별. `None` 이면 성별로 거르지 않는다(대상자 성별 미입력 등).
    candidate_genders: list[str] | None
    pref_age_min: int | None
    pref_age_max: int | None
    pref_region: str | None
    pref_height_min: int | None


class SoftCheck(BaseModel):
    """선호(랭킹) 조건 하나의 통과 근거. 값을 함께 실어 판단을 재현할 수 있게 한다."""

    code: str
    label: str
    passed: bool
    detail: str


class CandidateRow(BaseModel):
    id: int
    nickname: str | None
    gender: str | None
    age: int | None
    region: str | None
    height_cm: int | None
    #: 이미 열람한 후보. 하드필터는 통과하지만 다음 카드로는 나오지 않는다.
    already_unlocked: bool
    soft_checks: list[SoftCheck]
    #: 이 후보가 처음 포함되는 완화 단계. `None` 이면 어느 단계에서도 선호 조건을
    #: 만족하지 못한다는 뜻이다.
    stage_index: int | None


class ExcludedRow(BaseModel):
    id: int
    nickname: str | None
    gender: str | None
    #: 최초 탈락 조건. 조건 코드 순서는 서버가 고정한다(OI-MATCH-001 의 나열 순서).
    first_code: str
    codes: list[str]


class StageResult(BaseModel):
    """선호 조건 완화 단계 하나. 엔진은 후보가 나오는 첫 단계를 쓴다."""

    index: int
    label: str
    match_count: int
    applied: bool


class TargetVerification(BaseModel):
    target: TargetListItem
    snapshot: ConditionSnapshot
    hard_filters: list[FilterCode]
    soft_filters: list[FilterCode]

    total_members: int
    pool_count: int
    unlocked_count: int
    available_count: int

    #: **항상 0이어야 하는 두 숫자.** 엔진이 실제로 뽑은 후보군과 이 화면의 조건 판정을
    #: 맞대 본 결과다. 0이 아니면 둘 중 하나가 틀렸다는 뜻이고, 화면이 그 사실을 즉시
    #: 보여준다 (ADM-MATCH-002 체크포인트: 하드필터 위반 포함 0건).
    hard_filter_violations: int
    unexpected_exclusions: int

    stages: list[StageResult]
    #: 실제로 적용되는 완화 단계. 0보다 크면 선호 조건을 완화해 추천하고 있다는 뜻이다.
    applied_stage_index: int | None

    candidates: list[CandidateRow]
    candidates_total: int
    excluded: list[ExcludedRow]
    excluded_total: int


class RecalculateRequest(BaseModel):
    """재계산 사유. 비용이 드는 작업이라 감사 로그에 남는다(감사 대상 표 B-2)."""

    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)
