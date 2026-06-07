"""인연 카드 열람 서비스 — PRD 카드 모델.

  - 오늘의 인연: 하루 1장 무료(KST 일 단위). 최고 적합도 미열람 후보 자동 배정.
  - 추가 인연: 별 10개 차감, 하루 10장 한도. 다음 최고 적합도 후보 순차 공개.
  - 동일 사용자 재추천 불가: 이미 열람한 candidate 는 풀에서 제외.
  - 채팅 게이트: has_unlocked 로 "열람한 상대와만 채팅" 보장(chat 라우터 참조).

후보 선정·점수·카드 조립은 compatibility 모듈의 엔진을 재사용한다. PRD 6.1
"양방향 공개"(A↔B 대칭 매칭)는 전역 페어링 알고리즘이 필요한 별도 작업으로,
현재는 사용자별 "다음 최고점 미열람 후보"(비대칭 MVP)로 공개한다.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card_unlock import KIND_DAILY, KIND_EXTRA, CardUnlock
from app.models.user import User
from app.schemas.compatibility import MatchCandidate
from app.services.compatibility import (
    _build_card_for,
    _candidate_pool,
    _is_primary_face_verified,
    _snap_to_midnight_kst,
    calculate,
)

STAR_COST_PER_CARD = 10
EXTRA_DAILY_LIMIT = 10


async def _unlocked_ids(user_id: int, db: AsyncSession) -> set[int]:
    rows = await db.execute(
        select(CardUnlock.candidate_id).where(CardUnlock.user_id == user_id)
    )
    return set(rows.scalars().all())


async def _next_candidate(
    user: User, exclude_ids: set[int], db: AsyncSession
) -> User | None:
    """최고 적합도 미열람 이성 후보 1명. 풀이 비면 None."""
    pool = [c for c in await _candidate_pool(user, db) if c.id not in exclude_ids]
    if not pool:
        return None
    return max(pool, key=lambda c: calculate(user, c).score)


async def _reveal(user: User, candidate: User, db: AsyncSession) -> MatchCandidate:
    """열람한 카드는 항상 완전 공개(블라인드 없음)."""
    return _build_card_for(
        candidate,
        score=calculate(user, candidate).score,
        viewer_is_paid=True,
        is_paid_slot=False,
        is_face_verified=await _is_primary_face_verified(candidate, db),
    )


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
