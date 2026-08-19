"""관리자 회원 목록·상세 조회 (ADM-MEM-001/002).

이 모듈이 지키는 두 가지.

1. **마스킹은 여기서만 푼다.** 회원 응답을 만드는 경로가 하나라, 새 화면이 생겨도
   평문이 새지 않는다. 마스킹 규칙은 감사 로그가 쓰는 것(`services/audit.mask_sensitive`)을
   그대로 쓴다 — 같은 개인정보를 두 가지 기준으로 가리면 한쪽이 반드시 뒤처진다.
2. **"매칭 가능"의 정의는 한 곳에만 둔다.** 목록 필터는 SQL 로, 상세의 사유 표시는
   파이썬으로 판정하는데 둘이 어긋나면 "목록에는 매칭 가능인데 상세에는 불가 사유가
   달린 회원"이 나온다. 아래 두 함수는 같은 조건을 표현하며
   `tests/test_admin_members.py` 가 둘의 일치를 고정한다.
"""

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.block import UserBlock
from app.models.card_unlock import KIND_DAILY, KIND_EXTRA, CardUnlock
from app.models.moderation import UserStrike
from app.models.payment import StarOrder
from app.models.photo import UserPhoto
from app.models.star_ledger import StarLedger
from app.models.user import STATUS_ACTIVE, User
from app.schemas.admin_member import (
    MemberBasicInfo,
    MemberBilling,
    MemberCardUnlock,
    MemberDetail,
    MemberLedgerEntry,
    MemberListItem,
    MemberMatching,
    MemberOrder,
    MemberPillar,
    MemberProfile,
    MemberSaju,
    MemberSummary,
)
from app.services import saju as saju_service
from app.services.audit import mask_sensitive

# 상세 탭에 싣는 최근 목록의 길이. 회원 한 명의 전체 이력을 여기서 다 보여주려 하지
# 않는다 — 이력 화면은 T-E05(추천)·T-E06(결제)·T-E07(재화)에 따로 있다.
_RECENT_LIMIT = 20

_GENDERS = ("male", "female")


def matchable_condition() -> ColumnElement[bool]:
    """매칭 후보가 될 수 있는 회원을 고르는 SQL 조건.

    `gender` 를 `coalesce` 로 감싼 것은 3값 논리 때문이다. `gender IN (...)` 는
    NULL 일 때 NULL 이고, 그러면 `NOT (조건)` 도 NULL 이 되어 "매칭 불가만 보기"
    필터에서 성별 미입력 회원이 통째로 사라진다 — 정작 가장 봐야 할 회원들이다.
    """
    return and_(
        User.birth_date.is_not(None),
        User.photo_url.is_not(None),
        func.coalesce(User.gender, "").in_(_GENDERS),
        User.status == STATUS_ACTIVE,
        User.profile_hidden.is_(False),
    )


def matchable_blockers(user: User) -> list[str]:
    """이 회원이 매칭 후보가 되지 못하는 이유. 빈 목록이면 매칭 가능하다.

    `matchable_condition()` 과 같은 조건을 사람이 읽을 문장으로 뒤집은 것이다.
    """
    blockers = []
    if user.birth_date is None:
        blockers.append("생년월일 미입력")
    if user.photo_url is None:
        blockers.append("대표 사진 없음")
    if user.gender not in _GENDERS:
        blockers.append("성별 미입력")
    if user.status != STATUS_ACTIVE:
        blockers.append("정상 상태 아님")
    if user.profile_hidden:
        blockers.append("프로필 노출 중단")
    return blockers


def _masked(user: User) -> dict[str, str | None]:
    """연락처·생년월일 등 민감 필드의 마스킹된 값.

    이 앱에는 전화번호·이메일 컬럼이 없다. 회원을 밖에서 특정할 수 있는 값은
    카카오 식별자와 아이디뿐이라, "연락처"에 해당하는 것은 그 둘이다.
    """
    return mask_sensitive(
        {
            "kakao_id": user.kakao_id,
            "username": user.username,
            "birth_date": user.birth_date,
            "birth_time": user.birth_time,
            "birth_place": user.birth_place,
        }
    )


def list_item(user: User) -> MemberListItem:
    blockers = matchable_blockers(user)
    return MemberListItem(
        id=user.id,
        nickname=user.nickname,
        gender=user.gender,
        region=user.region,
        status=user.status,
        profile_hidden=user.profile_hidden,
        matchable=not blockers,
        matchable_blockers=blockers,
        star_balance=user.star_balance,
        is_paid=user.is_paid,
        created_at=user.created_at,
        **_masked(user),
    )


def build_list_query(
    *, q: str | None, status: str | None, matchable: bool | None
):
    """목록 조회 조건. 가입일 내림차순은 호출부가 아니라 여기서 고정한다."""
    stmt = select(User)
    if q:
        term = q.strip()
        if term:
            like = f"%{term.lower()}%"
            conditions = [func.lower(func.coalesce(User.nickname, "")).like(like)]
            if term.isdigit():
                # 동명이인을 가르는 값은 결국 id 다. 닉네임만 검색되면 화면에서
                # 찾은 회원을 다시 찾아 들어갈 방법이 없다.
                conditions.append(User.id == int(term))
            stmt = stmt.where(or_(*conditions))
    if status:
        stmt = stmt.where(User.status == status)
    if matchable is not None:
        condition = matchable_condition()
        stmt = stmt.where(condition if matchable else ~condition)
    # 같은 초에 가입한 회원이 있어도 순서가 흔들리지 않도록 id 로 한 번 더 정렬한다.
    return stmt.order_by(User.created_at.desc(), User.id.desc())


async def list_members(
    db: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    matchable: bool | None,
    page: int,
    size: int,
) -> tuple[int, list[MemberListItem]]:
    stmt = build_list_query(q=q, status=status, matchable=matchable)
    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = await db.execute(stmt.offset((page - 1) * size).limit(size))
    return int(total or 0), [list_item(u) for u in rows.scalars()]


def _saju(user: User) -> MemberSaju | None:
    """기존 사주 엔진 결과를 상세 탭용으로 줄인다. 생년월일이 없으면 없다."""
    if user.birth_date is None:
        return None
    result = saju_service.calculate(user)
    return MemberSaju(
        pillars=[
            MemberPillar(label=p.label, combined=p.combined) for p in result.pillars
        ],
        elements=result.element_profile.model_dump(),
        summary=result.summary,
    )


async def _matching(db: AsyncSession, user: User) -> MemberMatching:
    unlocks = (
        (
            await db.execute(
                select(CardUnlock)
                .where(CardUnlock.user_id == user.id)
                .order_by(CardUnlock.unlocked_at.desc())
                .limit(_RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await db.execute(
                select(CardUnlock.kind, func.count())
                .where(CardUnlock.user_id == user.id)
                .group_by(CardUnlock.kind)
            )
        ).all()
    )
    return MemberMatching(
        daily_count=counts.get(KIND_DAILY, 0),
        extra_count=counts.get(KIND_EXTRA, 0),
        blocking_count=await db.scalar(
            select(func.count()).where(UserBlock.blocker_id == user.id)
        )
        or 0,
        blocked_by_count=await db.scalar(
            select(func.count()).where(UserBlock.blocked_id == user.id)
        )
        or 0,
        strike_count=await db.scalar(
            select(func.count()).where(UserStrike.user_id == user.id)
        )
        or 0,
        recent_unlocks=[
            MemberCardUnlock(
                candidate_id=u.candidate_id, kind=u.kind, unlocked_at=u.unlocked_at
            )
            for u in unlocks
        ],
    )


async def _billing(db: AsyncSession, user: User) -> MemberBilling:
    orders = (
        (
            await db.execute(
                select(StarOrder)
                .where(StarOrder.user_id == user.id)
                .order_by(StarOrder.created_at.desc())
                .limit(_RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    entries = (
        (
            await db.execute(
                select(StarLedger)
                .where(StarLedger.user_id == user.id)
                .order_by(StarLedger.created_at.desc())
                .limit(_RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return MemberBilling(
        star_balance=user.star_balance,
        is_paid=user.is_paid,
        orders=[
            MemberOrder(
                order_id=o.order_id,
                product_id=o.product_id,
                amount=o.amount,
                star_amount=o.star_amount,
                status=o.status,
                created_at=o.created_at,
                paid_at=o.paid_at,
            )
            for o in orders
        ],
        ledger=[
            MemberLedgerEntry(
                entry_type=e.entry_type,
                reference_id=e.reference_id,
                amount=e.amount,
                balance_after=e.balance_after,
                created_at=e.created_at,
            )
            for e in entries
        ],
    )


async def build_detail(db: AsyncSession, user: User) -> MemberDetail:
    blockers = matchable_blockers(user)
    photo_count = (
        await db.scalar(select(func.count()).where(UserPhoto.user_id == user.id)) or 0
    )
    return MemberDetail(
        summary=MemberSummary(
            id=user.id,
            nickname=user.nickname,
            status=user.status,
            profile_hidden=user.profile_hidden,
            matchable=not blockers,
            matchable_blockers=blockers,
            chat_suspended_until=user.chat_suspended_until,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        basic=MemberBasicInfo(
            calendar_type=user.calendar_type,
            is_leap_month=user.is_leap_month,
            gender=user.gender,
            **_masked(user),
        ),
        profile=MemberProfile(
            nickname=user.nickname,
            bio=user.bio,
            photo_url=user.photo_url,
            photo_count=photo_count,
            height_cm=user.height_cm,
            mbti=user.mbti,
            job=user.job,
            region=user.region,
            smoking=user.smoking,
            drinking=user.drinking,
            religion=user.religion,
            pref_age_min=user.pref_age_min,
            pref_age_max=user.pref_age_max,
            pref_region=user.pref_region,
            pref_height_min=user.pref_height_min,
        ),
        saju=_saju(user),
        matching=await _matching(db, user),
        billing=await _billing(db, user),
    )
