"""관리자 재화 원장 스키마 (ADM-WALLET-001).

**잔액을 직접 받는 필드가 없다.** 이 화면에서 할 수 있는 변경은 "얼마를 지급/회수한다"
뿐이고, 그 결과 잔액은 원장이 계산한다. 목표 잔액을 받아 대입하는 필드를 하나라도 두면
원장과 잔액이 갈라질 수 있는 경로가 생긴다 — 그래서 금지를 주석이 아니라 **스키마의
부재**로 표현한다(결제 화면이 결제 상태 필드를 두지 않은 것과 같은 이유).

`request_id` 는 멱등키다. 관리자 앱이 폼을 열 때 한 번 만들어 두고 버튼을 몇 번 누르든
같은 값을 보낸다. 서버는 `(request_id, 유형)` Unique 로 두 번째부터를 무시한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 한 번에 움직일 수 있는 최대 수량. **정책 한도가 아니라 오타 방어다.**
# 운영 보상 한도(OI-PAY-006)는 아직 "논의 중"이라 여기에 정책을 적어 넣지 않았다.
# 한도가 정해지면 이 상수 하나만 바꾸면 된다(`.melobe/state/NEEDS_HUMAN.md` 참고).
MAX_ADJUST_AMOUNT = 10_000

_MIN_REASON = 2
_MAX_REASON = 200

AdjustDirection = Literal["grant", "revoke"]


class LedgerTypeMeta(BaseModel):
    """원장 유형 사전. 화면이 라벨을 자기가 들고 있지 않고 이것을 그린다.

    결제 화면의 이상 유형과 같은 방식이다. 화면에 라벨을 적어 두면 백엔드가 유형을
    하나 늘렸을 때 그 줄이 코드값으로 남거나, 이름이 서로 다른 두 개의 진실이 생긴다.
    """

    code: str
    label: str
    description: str


class WalletMember(BaseModel):
    """재화 화면이 필요로 하는 회원 정보. 개인정보는 담지 않는다(회원 상세의 몫)."""

    id: int
    nickname: str | None
    status: str
    star_balance: int


class WalletListItem(BaseModel):
    """회원 한 명의 재화 현황.

    `ledger_sum` 은 원장 `amount` 의 합이다. `star_balance` 와 다르면 **둘 중 하나가
    틀린 것**이고, 그 사실을 감추지 않는다. `diff = star_balance - ledger_sum`.
    """

    member: WalletMember
    ledger_sum: int
    entry_count: int
    diff: int
    balance_matches: bool
    last_entry_at: datetime | None


class WalletListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[WalletListItem]
    # 불일치 건수는 **필터와 무관하게 전체 기준**이다. 필터를 건 뒤에 세면
    # "불일치 0건"이 필터의 결과인지 사실인지 화면에서 구분할 수 없다.
    mismatch_total: int
    entry_types: list[LedgerTypeMeta]


class LedgerRow(BaseModel):
    """원장 한 줄. 전잔액을 함께 준다 — 연속성은 눈으로 확인되어야 한다.

    `balance_before` 는 저장된 값이 아니라 `balance_after - amount` 다. 따로 저장하면
    같은 사실이 두 컬럼에 적히고 언젠가 어긋난다.
    """

    id: int
    entry_type: str
    reference_id: str
    amount: int
    balance_before: int
    balance_after: int
    created_at: datetime


class ContinuityBreak(BaseModel):
    """전잔액/후잔액 연속성이 끊긴 지점.

    `expected_before` 는 직전 행의 `balance_after`(첫 행이면 0), `actual_before` 는
    이 행이 말하는 전잔액이다. 두 값이 다르면 그 사이에 기록되지 않은 변동이 있었거나
    누군가 잔액을 원장 밖에서 건드린 것이다.
    """

    entry_id: int
    expected_before: int
    actual_before: int
    created_at: datetime


class WalletAudit(BaseModel):
    """이 회원의 잔액이 믿을 만한가에 대한 답. 완료 조건의 "잔액 불일치 검증"이 이것이다.

    두 가지를 따로 본다.
    - `balance_matches`: 지금 잔액이 원장 합계와 같은가 (**결과**가 맞는가)
    - `continuity_breaks`: 원장을 순서대로 따라갔을 때 끊긴 곳이 있는가 (**과정**이 맞는가)

    합계는 맞는데 과정이 끊긴 경우가 실제로 가능하다(반대 부호의 누락 두 건). 하나로
    합치면 그런 원장을 정상이라고 답하게 된다.
    """

    star_balance: int
    ledger_sum: int
    diff: int
    balance_matches: bool
    entry_count: int
    continuity_breaks: list[ContinuityBreak]


class WalletDetail(BaseModel):
    member: WalletMember
    audit: WalletAudit
    total: int
    page: int
    size: int
    entries: list[LedgerRow]
    entry_types: list[LedgerTypeMeta]
    # 한 번에 움직일 수 있는 최대 수량(오타 방어). 화면이 자기 상수를 들고 있으면
    # 서버와 어긋나 "눌렀는데 422" 가 난다.
    max_amount: int
    # 회수 가능한 최대 수량 = 현재 잔액. 잔액을 음수로 만드는 회수는 없다
    # (users·star_ledger 양쪽 CHECK 제약이 마지막으로 막는다).
    max_revoke: int


class AdjustRequest(BaseModel):
    """운영 지급·회수 요청.

    `amount` 는 **항상 양수**이고 방향은 `direction` 이 정한다. 음수를 허용하면
    "회수인데 음수를 보내 지급이 되는" 부호 실수가 조용히 통과한다.
    """

    direction: AdjustDirection
    amount: int = Field(gt=0, le=MAX_ADJUST_AMOUNT)
    reason: str = Field(min_length=_MIN_REASON, max_length=_MAX_REASON)
    # 관리자 앱이 폼을 열 때 만든 값. 같은 폼에서 버튼을 몇 번 누르든 같은 값이 온다.
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class AdjustResponse(BaseModel):
    """조정 결과.

    `applied=False` 는 실패가 아니라 **같은 요청이 이미 반영되어 있었다**는 뜻이다
    (멱등키가 막았다). 오류로 돌려주면 운영자가 "안 됐나 보다" 하고 또 누른다.
    """

    applied: bool
    amount: int
    message: str
    detail: WalletDetail
