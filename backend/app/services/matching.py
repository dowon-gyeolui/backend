"""인연 카드 열람 서비스 — PRD 카드 모델.

  - 오늘의 인연: 하루 1장 무료(KST 일 단위). 최고 적합도 미열람 후보 자동 배정.
  - 추가 인연: 별 10개 차감, 하루 10장 한도. 다음 최고 적합도 후보 순차 공개.
  - 동일 사용자 재추천 불가: 이미 열람한 candidate 는 풀에서 제외.
  - 채팅 게이트: has_unlocked 로 "열람한 상대와만 채팅" 보장(chat 라우터 참조).

후보 선정·점수·카드 조립은 compatibility 모듈의 엔진을 재사용한다. PRD 6.1
"양방향 공개"(A↔B 대칭 매칭)는 전역 페어링 알고리즘이 필요한 별도 작업으로,
현재는 사용자별 "다음 최고점 미열람 후보"(비대칭 MVP)로 공개한다.
"""

import random
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

# 이상형 단계적 완화 경계값.
_HEIGHT_STEP = 5   # 키: -5cm씩 하강
_HEIGHT_FLOOR = 140
_AGE_STEP = 3      # 나이: ±3세씩 확대
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
    """후보 c 가 주어진 이상형 조건을 모두 만족하는가. None 조건은 미적용."""
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
    """이상형 필터를 엄격→느슨 순으로 누적 완화한 (age_min, age_max, region,
    height_min) 설정 리스트. 키 → 나이 → 지역 순으로 푼다.

    한 번 푼 조건은 이후 단계에서도 계속 풀린 상태로 유지(누적). 마지막
    설정은 항상 (None, None, None, None) = 기본 풀 전체.
    """
    a_min = user.pref_age_min
    a_max = user.pref_age_max
    region = user.pref_region
    h = user.pref_height_min

    configs: list[tuple[int | None, int | None, str | None, int | None]] = [
        (a_min, a_max, region, h)  # 0단계: 엄격
    ]

    # 1) 키 -5cm씩 하강 → 해제
    if h is not None:
        hh = h - _HEIGHT_STEP
        while hh >= _HEIGHT_FLOOR:
            configs.append((a_min, a_max, region, hh))
            hh -= _HEIGHT_STEP
        configs.append((a_min, a_max, region, None))

    # 2) 나이 ±3세씩 확대 → 해제 (키는 이미 해제된 상태)
    if a_min is not None and a_max is not None:
        lo, hi = a_min - _AGE_STEP, a_max + _AGE_STEP
        while lo > _AGE_FLOOR or hi < _AGE_CEIL:
            configs.append((max(lo, _AGE_FLOOR), min(hi, _AGE_CEIL), region, None))
            lo -= _AGE_STEP
            hi += _AGE_STEP
        configs.append((None, None, region, None))

    # 3) 지역 해제 = 기본 풀 전체
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
    """이상형으로 필터된 미열람 이성 후보 중 랜덤 1명. 풀이 비면 None.

    이상형 조건(키→나이→지역)을 단계적으로 완화하며, 후보가 1명이라도
    나오는 첫 단계에서 멈추고 그 풀에서 랜덤 선정한다.
    """
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
    # 마지막 설정이 기본 풀 전체이므로 여기 도달하지 않지만, 방어적으로.
    return random.choice(base)


async def _reveal(user: User, candidate: User, db: AsyncSession) -> MatchCandidate:
    """열람한 카드는 항상 완전 공개(블라인드 없음)."""
    card = _build_card_for(
        candidate,
        score=calculate(user, candidate).score,
        viewer_is_paid=True,
        is_paid_slot=False,
        is_face_verified=await _is_primary_face_verified(candidate, db),
    )
    card.photos = await _candidate_photos(candidate, db)
    return card


async def has_unlocked(user_id: int, candidate_id: int, db: AsyncSession) -> bool:
    """user 가 candidate 카드를 열람했는가 — 채팅·페어추천 게이트용."""
    row = await db.execute(
        select(CardUnlock.id)
        .where(CardUnlock.user_id == user_id)
        .where(CardUnlock.candidate_id == candidate_id)
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
    """오늘의 인연 1장(무료). 오늘 것이 있으면 그대로, 없으면 새로 배정."""
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
    """추가 인연 유료 열람 — 별 10개 차감 후 다음 후보 공개."""
    if await count_extra_today(user.id, db) >= EXTRA_DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"오늘의 추가 열람 한도({EXTRA_DAILY_LIMIT}장)를 모두 사용했어요.",
        )

    # 후보가 없으면 별이 있어도 의미 없으니 잔액보다 먼저 확인한다.
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
    """열람한 카드 목록(최근순) — 채팅 가능한 상대들."""
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
