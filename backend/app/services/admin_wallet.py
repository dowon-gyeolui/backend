"""관리자 재화 원장 조회·검증·운영 조정 (ADM-WALLET-001).

이 모듈이 지키는 세 가지.

1. **잔액을 대입하지 않는다.** 관리자가 보내는 것은 "얼마를 지급/회수한다"이고,
   잔액은 `services.star_ledger.record()` 가 원장과 같은 트랜잭션에서 계산한다.
   목표 잔액을 받아 `users.star_balance` 컬럼에 직접 대입하는 경로를 두면, 그 순간부터
   원장은 잔액의 설명이 아니라 잔액과 나란히 놓인 두 번째 기록이 된다
   (`tests/test_star_ledger.py::test_balance_is_written_only_through_the_ledger`
   가 그 대입을 원장 서비스 밖에서 하지 못하게 막는다).
2. **불일치를 감추지 않는다.** `users.star_balance` 와 원장 합계가 다르면 목록에서
   먼저 보이게 한다. 관리자 화면이 잔액만 보여주면 어긋난 사실이 화면에 영영
   나타나지 않는다.
3. **연속성은 합계와 따로 본다.** 합계가 맞는데 중간이 끊긴 원장이 실제로 가능하다
   (반대 부호의 누락 두 건). 하나로 합치면 그런 원장을 정상이라고 답하게 된다.

멱등키는 결제 재처리와 같은 구조다 — `(reference_id, entry_type)` Unique. 다만 운영
조정에는 주문번호처럼 밖에서 주어지는 값이 없어서, 관리자 앱이 폼을 열 때 만든
`request_id` 를 쓴다. 그래서 버튼을 연타해도 원장 행은 하나다(완료 조건).
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.star_ledger import (
    ENTRY_ADMIN_GRANT,
    ENTRY_ADMIN_REVOKE,
    ENTRY_CARD_UNLOCK,
    ENTRY_PURCHASE,
    ENTRY_TEST_TOPUP,
    StarLedger,
)
from app.models.user import User
from app.schemas.admin_wallet import (
    MAX_ADJUST_AMOUNT,
    ContinuityBreak,
    LedgerRow,
    LedgerTypeMeta,
    WalletAudit,
    WalletDetail,
    WalletListItem,
    WalletMember,
)
from app.services import star_ledger

# 원장 유형 사전. 화면은 라벨을 자기가 들고 있지 않고 이것을 그린다.
# 코드값은 `models/star_ledger.py` 의 상수와 같아야 한다 —
# `tests/test_admin_wallet.py::test_entry_type_dictionary_covers_every_type` 이 고정한다.
ENTRY_TYPES: tuple[LedgerTypeMeta, ...] = (
    LedgerTypeMeta(
        code=ENTRY_PURCHASE,
        label="결제 충전",
        description="결제 승인에 따라 지급된 스타예요. 근거는 주문번호예요.",
    ),
    LedgerTypeMeta(
        code=ENTRY_CARD_UNLOCK,
        label="카드 열람 차감",
        description="추가 인연 카드를 열람하면서 차감된 스타예요.",
    ),
    LedgerTypeMeta(
        code=ENTRY_TEST_TOPUP,
        label="개발용 충전",
        description="개발 환경에서만 열리는 무료 충전이에요. 운영에는 없어야 해요.",
    ),
    LedgerTypeMeta(
        code=ENTRY_ADMIN_GRANT,
        label="운영 지급",
        description="관리자가 사유를 남기고 지급한 스타예요.",
    ),
    LedgerTypeMeta(
        code=ENTRY_ADMIN_REVOKE,
        label="운영 회수",
        description="관리자가 사유를 남기고 회수한 스타예요.",
    ),
)

_NOT_FOUND = "존재하지 않는 회원이에요."

# 원장 합계. 원장이 한 줄도 없는 회원은 0 이다 — NULL 로 두면 잔액 0 인 신규 회원이
# 전부 "불일치"로 잡힌다.
_LEDGER_SUM = func.coalesce(func.sum(StarLedger.amount), 0)
_ENTRY_COUNT = func.count(StarLedger.id)
_LAST_ENTRY_AT = func.max(StarLedger.created_at)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 떨어뜨려 돌려준다. 저장은 항상 UTC 이므로 되붙인다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _member(user: User) -> WalletMember:
    return WalletMember(
        id=user.id,
        nickname=user.nickname,
        status=user.status,
        star_balance=user.star_balance,
    )


# --- 목록 ----------------------------------------------------------------------


def build_list_query(*, q: str | None, mismatch_only: bool):
    """회원별 재화 현황 조회.

    회원 기준 outer join 이라 **원장이 없는 회원도 한 줄 나온다.** 원장을 기준으로
    잡으면 "잔액은 있는데 원장이 통째로 없는" 회원이 목록에서 사라지는데, 그게 바로
    가장 먼저 찾아야 할 불일치다.
    """
    stmt = (
        select(
            User,
            _LEDGER_SUM.label("ledger_sum"),
            _ENTRY_COUNT.label("entry_count"),
            _LAST_ENTRY_AT.label("last_entry_at"),
        )
        .outerjoin(StarLedger, StarLedger.user_id == User.id)
        .group_by(User.id)
    )
    if q:
        term = q.strip()
        if term:
            like = f"%{term.lower()}%"
            conditions = [func.lower(func.coalesce(User.nickname, "")).like(like)]
            if term.isdigit():
                conditions.append(User.id == int(term))
            stmt = stmt.where(or_(*conditions))
    if mismatch_only:
        stmt = stmt.having(_LEDGER_SUM != User.star_balance)
    # 불일치를 맨 위로 올린다. 아래로 밀면 페이지를 넘겨야 보이고, 그러면 아무도 안 본다.
    return stmt.order_by(
        (_LEDGER_SUM == User.star_balance).asc(),
        _LAST_ENTRY_AT.desc().nulls_last(),
        User.id.desc(),
    )


async def list_wallets(
    db: AsyncSession,
    *,
    q: str | None,
    mismatch_only: bool,
    page: int,
    size: int,
) -> tuple[int, list[WalletListItem]]:
    stmt = build_list_query(q=q, mismatch_only=mismatch_only)
    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = await db.execute(stmt.offset((page - 1) * size).limit(size))
    items = [
        _list_item(user, int(ledger_sum or 0), int(entry_count or 0), _as_utc(last_at))
        for user, ledger_sum, entry_count, last_at in rows.all()
    ]
    return int(total or 0), items


def _list_item(
    user: User, ledger_sum: int, entry_count: int, last_entry_at: datetime | None
) -> WalletListItem:
    diff = user.star_balance - ledger_sum
    return WalletListItem(
        member=_member(user),
        ledger_sum=ledger_sum,
        entry_count=entry_count,
        diff=diff,
        balance_matches=diff == 0,
        last_entry_at=last_entry_at,
    )


async def mismatch_total(db: AsyncSession) -> int:
    """잔액이 원장 합계와 다른 회원 수. **필터와 무관하게 전체 기준이다.**"""
    inner = (
        select(User.id)
        .outerjoin(StarLedger, StarLedger.user_id == User.id)
        .group_by(User.id)
        .having(_LEDGER_SUM != User.star_balance)
        .subquery()
    )
    return int(await db.scalar(select(func.count()).select_from(inner)) or 0)


# --- 상세 · 검증 ----------------------------------------------------------------


async def load_member(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return user


async def _all_entries(db: AsyncSession, user_id: int) -> list[StarLedger]:
    """이 회원의 원장 전부, 기록 순서대로.

    연속성 검사는 페이지 단위로 할 수 없다. 2페이지만 보고 "끊겼다"고 말하려면
    1페이지 마지막 행을 알아야 하고, 그 순간 페이지 경계마다 거짓 경보가 난다.
    """
    rows = await db.execute(
        select(StarLedger)
        .where(StarLedger.user_id == user_id)
        .order_by(StarLedger.created_at.asc(), StarLedger.id.asc())
    )
    return list(rows.scalars())


def audit_entries(user: User, entries: list[StarLedger]) -> WalletAudit:
    """잔액 불일치 검증 — 완료 조건의 "잔액 불일치 검증 기능"이 이것이다.

    합계(결과)와 연속성(과정)을 따로 답한다. 원장을 처음부터 따라가며 각 행의
    전잔액(`balance_after - amount`)이 직전 행의 후잔액과 같은지 본다.
    """
    running = 0
    breaks: list[ContinuityBreak] = []
    for entry in entries:
        actual_before = entry.balance_after - entry.amount
        if actual_before != running:
            breaks.append(
                ContinuityBreak(
                    entry_id=entry.id,
                    expected_before=running,
                    actual_before=actual_before,
                    created_at=_as_utc(entry.created_at),
                )
            )
        running = entry.balance_after

    ledger_sum = sum(entry.amount for entry in entries)
    diff = user.star_balance - ledger_sum
    return WalletAudit(
        star_balance=user.star_balance,
        ledger_sum=ledger_sum,
        diff=diff,
        balance_matches=diff == 0,
        entry_count=len(entries),
        continuity_breaks=breaks,
    )


def _row(entry: StarLedger) -> LedgerRow:
    return LedgerRow(
        id=entry.id,
        entry_type=entry.entry_type,
        reference_id=entry.reference_id,
        amount=entry.amount,
        balance_before=entry.balance_after - entry.amount,
        balance_after=entry.balance_after,
        created_at=_as_utc(entry.created_at),
    )


async def build_detail(
    db: AsyncSession, user: User, *, page: int, size: int
) -> WalletDetail:
    """회원 한 명의 재화 상세. 원장은 **최신순**으로 페이지를 잘라 보여준다.

    검증은 전체 원장으로 하고 표시만 자른다. 검증까지 페이지에 맞춰 자르면
    "1페이지에서는 정상, 2페이지에서는 불일치" 같은 답이 나온다.
    """
    entries = await _all_entries(db, user.id)
    audit = audit_entries(user, entries)
    newest_first = list(reversed(entries))
    start = (page - 1) * size
    return WalletDetail(
        member=_member(user),
        audit=audit,
        total=len(entries),
        page=page,
        size=size,
        entries=[_row(entry) for entry in newest_first[start : start + size]],
        entry_types=list(ENTRY_TYPES),
        max_amount=MAX_ADJUST_AMOUNT,
        max_revoke=user.star_balance,
    )


# --- 운영 지급 · 회수 -----------------------------------------------------------


def entry_type_for(direction: str) -> str:
    return ENTRY_ADMIN_GRANT if direction == "grant" else ENTRY_ADMIN_REVOKE


async def adjust(
    db: AsyncSession,
    user: User,
    *,
    direction: str,
    amount: int,
    request_id: str,
) -> tuple[bool, str]:
    """운영 지급·회수를 원장 한 줄로 기록한다. `(반영됨, 안내 문구)`.

    - 반환 `True`  = 이번 호출로 반영됨
    - 반환 `False` = 같은 `request_id` 로 이미 반영되어 있었다(버튼 연타·재시도).
      **실패가 아니다.** 오류로 돌려주면 운영자가 또 누른다.

    회수는 잔액을 넘어설 수 없다. 음수 잔액을 허용할지(OI-PAY-007)는 아직 정해진 바가
    없고, 지금 스키마는 `users.star_balance >= 0` 과 `star_ledger.balance_after >= 0`
    두 CHECK 로 음수를 막고 있다. 정해지지 않은 것을 추측해 열어 두는 대신 **현재
    제약대로 거절**하고, 확정 요청은 `.melobe/state/NEEDS_HUMAN.md` 에 남겼다.
    """
    delta = amount if direction == "grant" else -amount
    if delta < 0 and user.star_balance + delta < 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"회수 수량이 현재 잔액보다 많아요. (현재 잔액 {user.star_balance}개)",
        )

    applied = await star_ledger.record(
        db,
        user,
        entry_type=entry_type_for(direction),
        reference_id=request_id,
        amount=delta,
    )
    await db.commit()
    await db.refresh(user)

    if not applied:
        return False, "이미 처리된 요청이에요. 잔액은 그대로예요."
    moved = "지급" if delta > 0 else "회수"
    return True, f"스타 {amount}개를 {moved}했어요. 현재 잔액 {user.star_balance}개예요."
