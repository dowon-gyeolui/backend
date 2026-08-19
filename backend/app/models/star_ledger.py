"""스타 재화 원장 모델(StarLedger) — 잔액 변동의 유일한 기록.

`users.star_balance` 는 이 원장 `amount` 합계와 같아야 한다. 잔액을 코드에서 직접
대입하지 말고 `services.star_ledger.record()` 를 거칠 것 — 그래야 "누가·언제·왜·얼마"
가 남고, QA 체크포인트 "잔액 직접 덮어쓰기 금지"가 지켜진다.

멱등키는 `(reference_id, entry_type)` Unique 다. 같은 주문번호로 지급 요청이 두 번
와도(버튼 연속 클릭·재시도·webhook 재전송) 원장 행이 하나만 생기므로 이중 지급이
원천 차단된다. **OI-PAY-002 는 아직 "논의 중"** 이며 DECISIONS.md C 의 추천안대로
구현했다 — 확정 요청은 `.melobe/state/NEEDS_HUMAN.md` 에 있다.

`balance_after` 는 감사용 스냅샷(전후잔액 연속성)이다. 음수를 CHECK 로 막아, 앱
레이어 검사를 빠뜨린 경로가 새로 생겨도 DB 가 마지막으로 거절한다.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.database import Base

# 지급유형(멱등키의 두 번째 축). 새 유형을 늘릴 때는 기존 값의 의미를 바꾸지 말 것 —
# 이미 쌓인 원장의 멱등키가 통째로 어긋난다.
ENTRY_PURCHASE = "purchase"        # 결제 승인에 따른 충전. reference_id = 주문번호
ENTRY_CARD_UNLOCK = "card_unlock"  # 추가 인연 카드 열람 차감. reference_id = "<사용자>:<후보>"
ENTRY_TEST_TOPUP = "test_topup"    # 개발용 무료 충전(운영에서는 라우트 자체가 404)
ENTRY_ADMIN_GRANT = "admin_grant"   # 운영 보상 지급. reference_id = 관리자 앱이 만든 요청 id
ENTRY_ADMIN_REVOKE = "admin_revoke"  # 운영 회수. reference_id = 관리자 앱이 만든 요청 id

# 운영 조정(ADM-WALLET-001)의 멱등키는 **요청 id** 다. 주문번호처럼 밖에서 주어지는 값이
# 없어서, 관리자 앱이 폼을 열 때 만든 id 를 그대로 reference_id 로 쓴다. 버튼을 연타해도
# 같은 id 가 가므로 Unique 제약이 두 번째부터를 걸러낸다(완료 조건: 중복 지급 방지).
ADMIN_ENTRY_TYPES = (ENTRY_ADMIN_GRANT, ENTRY_ADMIN_REVOKE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StarLedger(Base):
    __tablename__ = "star_ledger"
    __table_args__ = (
        UniqueConstraint(
            "reference_id", "entry_type", name="uq_star_ledger_idempotency"
        ),
        CheckConstraint(
            "balance_after >= 0", name="ck_star_ledger_balance_after_non_negative"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    entry_type = Column(String(20), nullable=False)
    reference_id = Column(String(64), nullable=False)

    amount = Column(Integer, nullable=False)         # 지급 +, 차감 −
    balance_after = Column(Integer, nullable=False)  # 이 행이 반영된 직후 잔액

    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
