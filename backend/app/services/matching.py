"""인연 카드 열람 서비스 — 오늘의 인연/추가 인연 배정, 열람·차단 상태 조회."""

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.block import UserBlock
from app.models.card_unlock import KIND_DAILY, KIND_EXTRA, CardUnlock
from app.models.user import User
from app.schemas.compatibility import MatchCandidate
from app.services.compatibility import (
    _build_card_for,
    _candidate_photos,
    _candidate_pool,
    _compute_age,
    _is_primary_face_verified,
    _snap_to_midnight_kst,
    calculate,
)

STAR_COST_PER_CARD = 10
EXTRA_DAILY_LIMIT = 10

_HEIGHT_STEP = 5
_HEIGHT_FLOOR = 140
_AGE_STEP = 3
_AGE_FLOOR = 18
_AGE_CEIL = 99


def _matches(
    c: User,
    *,
    age_min: int | None,
    age_max: int | None,
    region: str | None,
    height_min: int | None,
) -> bool:
    if age_min is not None and age_max is not None:
        age = _compute_age(c.birth_date)
        if age is None or not (age_min <= age <= age_max):
            return False
    if region is not None and c.region != region:
        return False
    if height_min is not None:
        if c.height_cm is None or c.height_cm < height_min:
            return False
    return True


def _relaxation_configs(
    user: User,
) -> list[tuple[int | None, int | None, str | None, int | None]]:
    a_min = user.pref_age_min
    a_max = user.pref_age_max
    region = user.pref_region
    h = user.pref_height_min

    configs: list[tuple[int | None, int | None, str | None, int | None]] = [
        (a_min, a_max, region, h)
    ]

    if h is not None:
        hh = h - _HEIGHT_STEP
        while hh >= _HEIGHT_FLOOR:
            configs.append((a_min, a_max, region, hh))
            hh -= _HEIGHT_STEP
        configs.append((a_min, a_max, region, None))

    if a_min is not None and a_max is not None:
        lo, hi = a_min - _AGE_STEP, a_max + _AGE_STEP
        while lo > _AGE_FLOOR or hi < _AGE_CEIL:
            configs.append((max(lo, _AGE_FLOOR), min(hi, _AGE_CEIL), region, None))
            lo -= _AGE_STEP
            hi += _AGE_STEP
        configs.append((None, None, region, None))

    if region is not None:
        configs.append((None, None, None, None))

    return configs


async def _unlocked_ids(user_id: int, db: AsyncSession) -> set[int]:
    rows = await db.execute(
        select(CardUnlock.candidate_id).where(CardUnlock.user_id == user_id)
    )
    return set(rows.scalars().all())


async def _next_candidate(
    user: User, exclude_ids: set[int], db: AsyncSession
) -> User | None:
    base = [c for c in await _candidate_pool(user, db) if c.id not in exclude_ids]
    if not base:
        return None
    for age_min, age_max, region, height_min in _relaxation_configs(user):
        pool = [
            c
            for c in base
            if _matches(
                c,
                age_min=age_min,
                age_max=age_max,
                region=region,
                height_min=height_min,
            )
        ]
        if pool:
            return random.choice(pool)
    return random.choice(base)


async def _reveal(user: User, candidate: User, db: AsyncSession) -> MatchCandidate:
    card = _build_card_for(
        candidate,
        score=calculate(user, candidate).score,
        viewer_is_paid=True,
        is_paid_slot=False,
        is_face_verified=await _is_primary_face_verified(candidate, db),
    )
    card.photos = await _candidate_photos(candidate, db)
    return card


async def has_unlocked(
    user_id: int,
    candidate_id: int,
    db: AsyncSession,
    within: Optional[timedelta] = None,
) -> bool:
    stmt = (
        select(CardUnlock.id)
        .where(CardUnlock.user_id == user_id)
        .where(CardUnlock.candidate_id == candidate_id)
        .limit(1)
    )
    if within is not None:
        cutoff = datetime.now(timezone.utc) - within
        stmt = stmt.where(CardUnlock.unlocked_at >= cutoff)
    row = await db.execute(stmt)
    return row.scalar_one_or_none() is not None


async def is_blocked(user_id: int, other_id: int, db: AsyncSession) -> bool:
    row = await db.execute(
        select(UserBlock.id)
        .where(
            or_(
                and_(
                    UserBlock.blocker_id == user_id,
                    UserBlock.blocked_id == other_id,
                ),
                and_(
                    UserBlock.blocker_id == other_id,
                    UserBlock.blocked_id == user_id,
                ),
            )
        )
        .limit(1)
    )
    return row.scalar_one_or_none() is not None


async def count_extra_today(user_id: int, db: AsyncSession) -> int:
    midnight = _snap_to_midnight_kst(datetime.now(timezone.utc))
    cnt = await db.execute(
        select(func.count(CardUnlock.id))
        .where(CardUnlock.user_id == user_id)
        .where(CardUnlock.kind == KIND_EXTRA)
        .where(CardUnlock.unlocked_at >= midnight)
    )
    return int(cnt.scalar_one() or 0)


async def _daily_today(user_id: int, db: AsyncSession) -> CardUnlock | None:
    midnight = _snap_to_midnight_kst(datetime.now(timezone.utc))
    row = await db.execute(
        select(CardUnlock)
        .where(CardUnlock.user_id == user_id)
        .where(CardUnlock.kind == KIND_DAILY)
        .where(CardUnlock.unlocked_at >= midnight)
        .limit(1)
    )
    return row.scalar_one_or_none()


async def get_today_card(user: User, db: AsyncSession) -> MatchCandidate | None:
    existing = await _daily_today(user.id, db)
    if existing is not None:
        candidate = await db.get(User, existing.candidate_id)
        return await _reveal(user, candidate, db) if candidate else None

    candidate = await _next_candidate(user, await _unlocked_ids(user.id, db), db)
    if candidate is None:
        return None
    db.add(CardUnlock(user_id=user.id, candidate_id=candidate.id, kind=KIND_DAILY))
    await db.commit()
    return await _reveal(user, candidate, db)


async def unlock_extra(user: User, db: AsyncSession) -> MatchCandidate:
    if await count_extra_today(user.id, db) >= EXTRA_DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"오늘의 추가 열람 한도({EXTRA_DAILY_LIMIT}장)를 모두 사용했어요.",
        )

    candidate = await _next_candidate(user, await _unlocked_ids(user.id, db), db)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="더 이상 추천할 인연이 없어요.",
        )
    if user.star_balance < STAR_COST_PER_CARD:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="스타가 부족합니다.",
        )

    user.star_balance -= STAR_COST_PER_CARD
    db.add(CardUnlock(user_id=user.id, candidate_id=candidate.id, kind=KIND_EXTRA))
    await db.commit()
    return await _reveal(user, candidate, db)


async def list_unlocked(user: User, db: AsyncSession) -> list[MatchCandidate]:
    rows = await db.execute(
        select(CardUnlock)
        .where(CardUnlock.user_id == user.id)
        .order_by(CardUnlock.unlocked_at.desc())
    )
    cards: list[MatchCandidate] = []
    for row in rows.scalars().all():
        candidate = await db.get(User, row.candidate_id)
        if candidate is not None:
            cards.append(await _reveal(user, candidate, db))
    return cards