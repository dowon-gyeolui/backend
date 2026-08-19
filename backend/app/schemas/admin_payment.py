"""관리자 결제 목록·상세 스키마 (ADM-PAY-001/002).

이 화면에는 **바꿀 수 있는 값이 거의 없다.** 결제 성공 여부와 금액의 원천은 결제사이고
(OI-PAY-001), 우리 DB 의 주문 행은 그 사본이다. 그래서 요청 스키마에 결제 상태를
직접 넣는 필드도, 금액을 고치는 필드도 두지 않았다 — "금지"를 코드 주석이 아니라
스키마의 부재로 표현한다. 관리자가 할 수 있는 변경은 **재화 지급을 다시 시도하는 것**
하나뿐이고, 그것도 결제사가 성공을 확인해 준 주문에만 통한다.

민감정보는 담지 않는다. 결제 줄에 붙는 회원 정보는 id·닉네임·상태까지다. 생년월일과
연락처는 회원 상세(ADM-MEM-002)에서 사유를 남겨야 볼 수 있고, 이 화면이 그 통로를
우회하게 두지 않는다(매칭 화면과 같은 이유).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.payment import STATUS_FAILED, STATUS_PAID, STATUS_PENDING

OrderStatus = Literal[STATUS_PENDING, STATUS_PAID, STATUS_FAILED]

# 지급 이상 유형. 목록 필터의 값이자 상세의 배지이며, 값의 의미는
# `services/admin_payments` 가 코드·라벨·설명을 함께 내려 준다.
IssueCode = Literal[
    "not_credited", "orphan_credit", "amount_mismatch", "stale_pending"
]

# "이상 있는 주문만" 을 고르는 값. 유형별로 나눠 보기 전에 전체를 한 번에 보는 것이
# 실제 사용 흐름이라 별도 값으로 둔다.
IssueFilter = Literal[
    "any", "not_credited", "orphan_credit", "amount_mismatch", "stale_pending"
]

_MIN_REASON = 2
_MAX_REASON = 200


class IssueMeta(BaseModel):
    """이상 유형 하나의 사전. 화면은 라벨을 자기가 들고 있지 않고 이것을 그린다."""

    code: IssueCode
    label: str
    description: str


class PaymentMember(BaseModel):
    """결제한 회원. 결제 화면이 필요로 하는 최소한만 싣는다."""

    id: int
    nickname: str | None
    status: str
    star_balance: int


class PaymentListItem(BaseModel):
    order_id: str
    product_id: str
    product_name: str | None
    amount: int
    star_amount: int
    status: OrderStatus
    payment_key: str | None
    created_at: datetime
    paid_at: datetime | None
    credited_stars: int | None
    credited_at: datetime | None
    issues: list[IssueCode]
    member: PaymentMember


class IssueCount(BaseModel):
    code: IssueCode
    count: int


class PaymentListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[PaymentListItem]
    issues: list[IssueMeta]
    # 유형별 건수는 필터를 걸기 **전** 전체 기준이다. 필터를 건 뒤의 건수를 주면
    # "이상 3건" 배지가 필터를 옮길 때마다 0 으로 바뀌어 아무 쓸모가 없어진다.
    issue_counts: list[IssueCount]


class LedgerEntry(BaseModel):
    entry_type: str
    reference_id: str
    amount: int
    balance_after: int
    created_at: datetime


class PaymentDetail(BaseModel):
    """주문 한 건. 결제사 상태는 여기 없다 — 별도 대조 요청으로만 가져온다.

    상세를 열 때마다 결제사에 물으면 목록·상세 로딩이 결제사 장애에 묶이고, 관리자가
    화면을 새로고침할 때마다 바깥 호출이 늘어난다. 대조는 사람이 버튼을 눌렀을 때만 한다.
    """

    order: PaymentListItem
    ledger: list[LedgerEntry]
    can_regrant: bool
    regrant_blocker: str | None
    # 목록과 같은 사전을 함께 싣는다. 상세만 보고 들어온 화면(목록을 거치지 않은
    # 새 탭·북마크)이 이상 유형을 코드로 보여주지 않게 하려는 것이다.
    issues: list[IssueMeta]


class ProviderPayment(BaseModel):
    """결제사 대조 결과 (OI-PAY-001: 결제사가 원천).

    `available=False` 는 "결제가 없다"가 아니라 **"물어보지 못했다"** 이다. 둘을 같은
    값으로 표현하면 결제사 장애가 "결제 없음"으로 보여 멀쩡한 주문을 실패로 판단하게 된다.
    """

    available: bool
    reason: str | None
    status: str | None
    total_amount: int | None
    payment_key: str | None
    method: str | None
    approved_at: str | None
    amount_matches: bool | None
    success_matches: bool | None


class RegrantRequest(BaseModel):
    """지급 재처리 사유. 금전 가치가 움직이는 액션이라 감사 로그에 반드시 남는다(B-2)."""

    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)


class RegrantResponse(BaseModel):
    """재처리 결과.

    `granted=False` 는 실패가 아니라 **이미 지급되어 있었다**는 뜻이다(멱등키가 막았다).
    버튼을 연속으로 누르면 두 번째부터 여기로 온다 — 화면은 이것을 오류가 아니라
    "이미 지급됨"으로 보여야 한다.
    """

    granted: bool
    credited_stars: int
    message: str
    detail: PaymentDetail
