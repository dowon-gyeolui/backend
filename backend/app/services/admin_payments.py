"""관리자 결제 목록·상세 조회와 지급 재처리 (ADM-PAY-001/002).

이 모듈이 지키는 세 가지.

1. **결제사가 원천이다(OI-PAY-001).** 우리 DB 의 `star_orders.status` 는 사본이라
   관리자가 손으로 고칠 수 있게 두지 않는다. 상태가 바뀌는 유일한 경로는
   `regrant()` 이고, 그것도 결제사에 물어 `DONE` 과 금액 일치를 확인한 뒤에만 움직인다.
   확인하지 못하면(키 없음·네트워크 오류) **아무것도 하지 않는다** — 물어보지 못한 것을
   "결제 안 됨"으로 읽으면 결제사 장애 때 멀쩡한 주문을 망가뜨린다.
2. **지급은 승인 경로와 같은 멱등키를 쓴다.** `(주문번호, purchase)` Unique 라
   재처리 버튼을 연타해도 원장 행은 하나뿐이다(T-B06). 재처리가 자기만의 지급 경로를
   새로 만들면 그 순간 이중 지급이 가능해진다.
3. **이상 판정이 SQL 과 파이썬 두 벌이다 — 일부러 그렇다.** 목록 필터는 SQL 이어야
   페이지네이션이 맞고, 줄마다 붙는 배지는 파이썬이어야 여러 유형을 한 번에 말할 수
   있다. 두 벌이 어긋나면 "목록엔 이상, 상세엔 정상"이 나오므로
   `tests/test_admin_payments.py::test_sql_and_python_issue_detection_agree` 가
   둘의 일치를 고정한다. (회원 화면의 "매칭 가능" 판정과 같은 구조다)
"""

from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.models.payment import STATUS_PAID, STATUS_PENDING, StarOrder
from app.models.star_ledger import ENTRY_PURCHASE, StarLedger
from app.models.user import User
from app.schemas.admin_payment import (
    IssueCount,
    IssueMeta,
    LedgerEntry,
    PaymentDetail,
    PaymentListItem,
    PaymentMember,
    ProviderPayment,
)
from app.services import payments as payments_service
from app.services import star_ledger

_KST = timezone(timedelta(hours=9))

# 미확정 주문을 "오래됐다"고 부르기까지의 시간.
# 결제 정리(`payments._ABANDON_AFTER`, 10분)보다 넉넉히 잡았다. 정리는 **사용자가 앱을
# 다시 열었을 때** 도는 경로라, 그보다 짧게 잡으면 아직 정리가 돌 기회조차 없었던 주문이
# 이상 목록을 가득 채운다. 여기 걸리는 주문은 "사용자가 돌아오지 않았다"는 뜻이고,
# 그때는 사람이 결제사와 대조해야 한다.
_STALE_PENDING_AFTER = timedelta(minutes=30)

ISSUE_NOT_CREDITED = "not_credited"
ISSUE_ORPHAN_CREDIT = "orphan_credit"
ISSUE_AMOUNT_MISMATCH = "amount_mismatch"
ISSUE_STALE_PENDING = "stale_pending"

ISSUES: tuple[IssueMeta, ...] = (
    IssueMeta(
        code=ISSUE_NOT_CREDITED,
        label="재화 미지급",
        description="결제는 성공했는데 스타가 지급되지 않았어요. 지급 재처리 대상이에요.",
    ),
    IssueMeta(
        code=ISSUE_ORPHAN_CREDIT,
        label="미확정 지급",
        description="결제가 성공으로 확정되지 않았는데 지급 기록이 있어요. 결제사와 대조가 필요해요.",
    ),
    IssueMeta(
        code=ISSUE_AMOUNT_MISMATCH,
        label="지급 수량 불일치",
        description="지급된 스타 수가 주문 수량과 달라요.",
    ),
    IssueMeta(
        code=ISSUE_STALE_PENDING,
        label="장기 미확정",
        description="30분이 넘도록 결제 결과가 확정되지 않았어요. 결제사와 대조가 필요해요.",
    ),
)

ISSUE_CODES: tuple[str, ...] = tuple(issue.code for issue in ISSUES)

# 주문에 딸린 충전 원장. `(reference_id, entry_type)` 이 Unique 라 outer join 결과는
# 주문당 0 또는 1행이고, 그래서 집계 없이 그대로 붙여 쓸 수 있다.
_LEDGER = aliased(StarLedger, name="purchase_ledger")

_NOT_FOUND = "존재하지 않는 주문이에요."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 떨어뜨려 돌려준다. 저장은 항상 UTC 이므로 되붙인다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


# --- 이상 판정 -----------------------------------------------------------------


def issue_condition(code: str, *, now: datetime) -> ColumnElement[bool]:
    """이상 유형 하나를 고르는 SQL 조건. `issues_for()` 와 같은 것을 표현한다."""
    if code == ISSUE_NOT_CREDITED:
        return and_(StarOrder.status == STATUS_PAID, _LEDGER.id.is_(None))
    if code == ISSUE_ORPHAN_CREDIT:
        return and_(StarOrder.status != STATUS_PAID, _LEDGER.id.is_not(None))
    if code == ISSUE_AMOUNT_MISMATCH:
        return and_(_LEDGER.id.is_not(None), _LEDGER.amount != StarOrder.star_amount)
    if code == ISSUE_STALE_PENDING:
        return and_(
            StarOrder.status == STATUS_PENDING,
            StarOrder.created_at < now - _STALE_PENDING_AFTER,
        )
    raise ValueError(f"알 수 없는 이상 유형: {code!r}")


def any_issue_condition(*, now: datetime) -> ColumnElement[bool]:
    return or_(*(issue_condition(code, now=now) for code in ISSUE_CODES))


def issues_for(
    order: StarOrder, ledger: StarLedger | None, *, now: datetime
) -> list[str]:
    """이 주문에 붙는 이상 유형 전부. 빈 목록이면 정상이다.

    여러 개가 동시에 붙을 수 있다(예: 미확정인데 지급됐고 수량도 다르다). 하나만
    돌려주면 화면이 첫 번째 사유만 보여주고 나머지는 아무도 모르는 채로 남는다.
    """
    found = []
    if order.status == STATUS_PAID and ledger is None:
        found.append(ISSUE_NOT_CREDITED)
    if order.status != STATUS_PAID and ledger is not None:
        found.append(ISSUE_ORPHAN_CREDIT)
    if ledger is not None and ledger.amount != order.star_amount:
        found.append(ISSUE_AMOUNT_MISMATCH)
    created = _as_utc(order.created_at)
    if (
        order.status == STATUS_PENDING
        and created is not None
        and created < now - _STALE_PENDING_AFTER
    ):
        found.append(ISSUE_STALE_PENDING)
    # 선언 순서를 그대로 따른다. 화면의 배지 순서가 실행마다 흔들리지 않게 한다.
    return [code for code in ISSUE_CODES if code in found]


# --- 목록 ----------------------------------------------------------------------


def _list_item(
    order: StarOrder, ledger: StarLedger | None, user: User, *, now: datetime
) -> PaymentListItem:
    product = payments_service.PRODUCT_CATALOG.get(order.product_id)
    return PaymentListItem(
        order_id=order.order_id,
        product_id=order.product_id,
        # 카탈로그에서 사라진 옛 상품이면 이름이 없다. 그때는 화면이 상품 코드를
        # 그대로 보여주게 둔다 — 빈칸으로 덮으면 무엇을 판 주문인지 알 수 없다.
        product_name=None if product is None else product["name"],
        amount=order.amount,
        star_amount=order.star_amount,
        status=order.status,
        payment_key=order.payment_key,
        created_at=order.created_at,
        paid_at=order.paid_at,
        credited_stars=None if ledger is None else ledger.amount,
        credited_at=None if ledger is None else ledger.created_at,
        issues=issues_for(order, ledger, now=now),
        member=PaymentMember(
            id=user.id,
            nickname=user.nickname,
            status=user.status,
            star_balance=user.star_balance,
        ),
    )


def _base_query():
    """주문 + 충전 원장 + 회원을 한 줄로 붙인 조회.

    회원은 inner join 이다. 탈퇴는 회원 행을 지우는 hard delete 라 남은 주문이 있으면
    FK 가 삭제를 막고, 그래서 "주인 없는 주문"은 존재할 수 없다.
    """
    return (
        select(StarOrder, _LEDGER, User)
        .join(User, User.id == StarOrder.user_id)
        .outerjoin(
            _LEDGER,
            and_(
                _LEDGER.reference_id == StarOrder.order_id,
                _LEDGER.entry_type == ENTRY_PURCHASE,
            ),
        )
    )


def _kst_day_bounds(day_from: date | None, day_to: date | None) -> tuple[datetime | None, datetime | None]:
    """KST 달력 날짜를 UTC 구간으로. 관리자가 말하는 "8월 19일"은 한국 날짜다."""
    start = (
        None
        if day_from is None
        else datetime.combine(day_from, datetime.min.time(), _KST).astimezone(timezone.utc)
    )
    end = (
        None
        if day_to is None
        else datetime.combine(day_to + timedelta(days=1), datetime.min.time(), _KST).astimezone(
            timezone.utc
        )
    )
    return start, end


def build_list_query(
    *,
    q: str | None,
    status: str | None,
    issue: str | None,
    member_id: int | None,
    day_from: date | None,
    day_to: date | None,
    now: datetime,
):
    """목록 조회 조건. 결제일 내림차순은 호출부가 아니라 여기서 고정한다."""
    stmt = _base_query()
    if q:
        term = q.strip()
        if term:
            like = f"%{term.lower()}%"
            conditions = [
                func.lower(StarOrder.order_id).like(like),
                func.lower(func.coalesce(StarOrder.payment_key, "")).like(like),
                func.lower(func.coalesce(User.nickname, "")).like(like),
            ]
            if term.isdigit():
                conditions.append(User.id == int(term))
            stmt = stmt.where(or_(*conditions))
    if status:
        stmt = stmt.where(StarOrder.status == status)
    if member_id is not None:
        stmt = stmt.where(StarOrder.user_id == member_id)
    if issue:
        stmt = stmt.where(
            any_issue_condition(now=now)
            if issue == "any"
            else issue_condition(issue, now=now)
        )
    start, end = _kst_day_bounds(day_from, day_to)
    if start is not None:
        stmt = stmt.where(StarOrder.created_at >= start)
    if end is not None:
        stmt = stmt.where(StarOrder.created_at < end)
    # 같은 초에 만들어진 주문이 있어도 순서가 흔들리지 않도록 id 로 한 번 더 정렬한다.
    return stmt.order_by(StarOrder.created_at.desc(), StarOrder.id.desc())


async def issue_counts(db: AsyncSession, *, now: datetime) -> list[IssueCount]:
    """유형별 이상 건수. **필터와 무관하게 전체 기준이다.**

    질의 한 번으로 네 개를 센다. 유형마다 count 를 따로 돌리면 화면 한 번에 다섯 번의
    왕복이 생기고, 그 사이에 새 주문이 들어오면 배지 합이 목록 건수와 어긋난다.
    """
    columns = [
        func.sum(case((issue_condition(code, now=now), 1), else_=0)).label(code)
        for code in ISSUE_CODES
    ]
    row = (
        await db.execute(
            select(*columns)
            .select_from(StarOrder)
            .join(User, User.id == StarOrder.user_id)
            .outerjoin(
                _LEDGER,
                and_(
                    _LEDGER.reference_id == StarOrder.order_id,
                    _LEDGER.entry_type == ENTRY_PURCHASE,
                ),
            )
        )
    ).one()
    return [
        IssueCount(code=code, count=int(value or 0))
        for code, value in zip(ISSUE_CODES, row)
    ]


async def list_orders(
    db: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    issue: str | None,
    member_id: int | None,
    day_from: date | None,
    day_to: date | None,
    page: int,
    size: int,
) -> tuple[int, list[PaymentListItem]]:
    now = _utcnow()
    stmt = build_list_query(
        q=q,
        status=status,
        issue=issue,
        member_id=member_id,
        day_from=day_from,
        day_to=day_to,
        now=now,
    )
    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = await db.execute(stmt.offset((page - 1) * size).limit(size))
    items = [
        _list_item(order, ledger, user, now=now) for order, ledger, user in rows.all()
    ]
    return int(total or 0), items


# --- 상세 ----------------------------------------------------------------------


async def load_order(
    db: AsyncSession, order_id: str
) -> tuple[StarOrder, StarLedger | None, User] | None:
    row = (
        await db.execute(_base_query().where(StarOrder.order_id == order_id))
    ).first()
    return None if row is None else (row[0], row[1], row[2])


def _regrant_blocker(ledger: StarLedger | None) -> str | None:
    """재처리 버튼을 막는 이유. `None` 이면 눌러도 된다.

    막는 경우는 **이미 지급된 주문** 하나뿐이다. 눌러도 멱등키가 막아 주지만, 누를 수
    있게 두면 운영자가 "한 번 더 눌러 보자"를 하게 되고 그 습관은 멱등키가 없는 다음
    기능에서 사고가 된다.

    아직 성공으로 확정되지 않은 주문(PENDING)은 막지 않는다. 결제사가 성공을 확인해
    주면 재처리가 우리 상태까지 맞추는 것이 옳고(OI-PAY-001), 사용자가 앱으로 돌아오지
    않아 정리(`payments.reconcile_pending_orders`)가 영영 돌지 않는 주문은 그 경로가
    아니면 아무도 고칠 수 없다.
    """
    return "이미 지급된 주문이에요." if ledger is not None else None


async def build_detail(
    db: AsyncSession, order: StarOrder, ledger: StarLedger | None, user: User
) -> PaymentDetail:
    """주문 상세. 원장은 이 주문의 충전 건만 싣는다.

    회원의 원장 전체는 재화 화면(ADM-WALLET-001, T-E07)의 몫이다. 여기서 다 보여주면
    같은 표가 두 화면에 생기고, 둘 중 하나는 반드시 뒤처진다.
    """
    rows = (
        await db.execute(
            select(StarLedger)
            .where(
                StarLedger.user_id == user.id,
                StarLedger.reference_id == order.order_id,
            )
            .order_by(StarLedger.created_at.asc(), StarLedger.id.asc())
        )
    ).scalars()
    blocker = _regrant_blocker(ledger)
    return PaymentDetail(
        order=_list_item(order, ledger, user, now=_utcnow()),
        ledger=[
            LedgerEntry(
                entry_type=entry.entry_type,
                reference_id=entry.reference_id,
                amount=entry.amount,
                balance_after=entry.balance_after,
                created_at=entry.created_at,
            )
            for entry in rows
        ],
        can_regrant=blocker is None,
        regrant_blocker=blocker,
        issues=list(ISSUES),
    )


# --- 결제사 대조 ---------------------------------------------------------------


async def fetch_provider_payment(order: StarOrder) -> ProviderPayment:
    """결제사에 이 주문의 결제를 물어본다. **읽기 전용 — 아무것도 바꾸지 않는다.**

    `available=False` 와 `status="NO_PAYMENT"` 는 다르다. 앞은 "물어보지 못했다",
    뒤는 "결제사에 기록이 없다"이다. 이 구분이 무너지면 결제사 장애가 곧 "결제 없음"이
    되어, 재처리 판단과 주문 정리가 통째로 틀어진다.
    """
    if not settings.toss_secret_key:
        return ProviderPayment(
            available=False,
            reason="결제사 키가 설정되지 않아 대조할 수 없어요.",
            status=None,
            total_amount=None,
            payment_key=None,
            method=None,
            approved_at=None,
            amount_matches=None,
            success_matches=None,
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        payment = await payments_service._query_toss_order(client, order.order_id)

    if payment is None:
        return ProviderPayment(
            available=False,
            reason="결제사에 연결하지 못했어요. 잠시 후 다시 시도해주세요.",
            status=None,
            total_amount=None,
            payment_key=None,
            method=None,
            approved_at=None,
            amount_matches=None,
            success_matches=None,
        )

    provider_status = payment.get("status")
    total_amount = payment.get("totalAmount")
    return ProviderPayment(
        available=True,
        reason=None,
        status=provider_status,
        total_amount=total_amount,
        payment_key=payment.get("paymentKey"),
        method=payment.get("method"),
        approved_at=payment.get("approvedAt"),
        amount_matches=None if total_amount is None else total_amount == order.amount,
        # **상태 문자열이 같은가가 아니라 "성공 여부"가 맞는가다.** 결제사가 성공이라
        # 하는데 우리 주문이 성공이 아니면 여기서 False 가 되고, 그것이 곧 "지급 이상"의
        # 원인이자 재처리가 고쳐야 할 상태다. 둘 다 성공이 아니면(예: CANCELED / PENDING)
        # 맞는 것으로 본다 — 그 주문은 지급 대상이 아니고, 닫는 일은 결제 정리의 몫이다.
        success_matches=(provider_status == payments_service.TOSS_STATUS_DONE)
        == (order.status == STATUS_PAID),
    )


# --- 지급 재처리 ---------------------------------------------------------------


async def regrant(
    db: AsyncSession, order: StarOrder, ledger: StarLedger | None, user: User
) -> tuple[bool, int, str]:
    """결제사 확인을 거쳐 스타를 다시 지급한다. `(지급됨, 지급 스타, 안내 문구)`.

    **결제사가 성공이라고 답한 주문에만 지급한다.** 우리 DB 의 상태를 믿고 지급하면,
    정리 로직이 잘못 닫아 둔 주문이나 손으로 바꾼 상태가 그대로 돈이 된다. 물어보지
    못했을 때(키 없음·네트워크)는 지급도 상태 변경도 하지 않고 503 으로 물러난다 —
    "확인 못 함"을 "확인됨"으로 취급하는 순간 이 버튼은 무료 스타 발급기가 된다.

    지급 자체는 승인 경로와 **같은 멱등키**(`주문번호 + purchase`)를 쓴다. 그래서 버튼을
    연타해도, 사용자 쪽 정리와 겹쳐도 원장 행은 하나이고 스타는 한 번만 들어간다.
    """
    if ledger is not None:
        # 여기서 이미 걸러 낸다. 아래로 내려가면 결제사에 헛물음이 하나 생긴다.
        return False, 0, "이미 지급된 주문이라 추가 지급은 하지 않았어요."

    provider = await fetch_provider_payment(order)
    if not provider.available:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=provider.reason or "결제사에 확인하지 못해 재처리하지 않았어요.",
        )
    if provider.status != payments_service.TOSS_STATUS_DONE:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"결제사에서 성공으로 확인되지 않은 주문이에요(결제사 상태: {provider.status}).",
        )
    if provider.total_amount != order.amount:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "결제사 금액과 주문 금액이 달라요"
                f"(결제사 {provider.total_amount}원 / 주문 {order.amount}원). "
                "금액은 이 화면에서 고칠 수 없어요 — 결제사에서 확인해주세요."
            ),
        )

    # 결제사가 성공이라 확인해 준 주문이므로 우리 상태를 그쪽에 맞춘다. 관리자가 고른
    # 값이 아니라 결제사가 준 값이라는 점이 "결제 성공 상태 임의 변경 금지"와 갈리는 지점이다.
    order.status = STATUS_PAID
    if order.payment_key is None:
        order.payment_key = provider.payment_key
    if order.paid_at is None:
        order.paid_at = _utcnow()

    granted = await star_ledger.record(
        db,
        user,
        entry_type=ENTRY_PURCHASE,
        reference_id=order.order_id,
        amount=order.star_amount,
    )
    await db.commit()

    if not granted:
        # 멱등키가 막았다 = 그 사이에 다른 경로(승인·정리·다른 관리자)가 이미 지급했다.
        return False, 0, "이미 지급되어 있어 추가 지급은 하지 않았어요."
    return True, order.star_amount, f"스타 {order.star_amount}개를 지급했어요."
