"""홈 전광판(스탯 티커) 데이터.

가입자/성비/채팅방/오늘 매칭/최근 활동 등 단순 집계 + 개인화 사주 분포
(나와 같은 일간/오행 회원 수)를 한 번에 반환한다.

비용 의식: 전 회원 사주 분포 집계는 무겁다(회원마다 일주 계산 + 오행 분포).
그래서 viewer 와 무관한 전역 집계는 모듈 레벨 캐시에 TTL 로 보관하고,
개인화 수치는 캐시된 분포 dict 룩업으로 싸게 처리한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card_unlock import CardUnlock
from app.models.chat import Message
from app.models.user import User
from app.services.compatibility import _dominant_element, _snap_to_midnight_kst
from app.services.saju import calculate as calculate_saju
from app.services.saju_engine import _day_pillar

# 전역 집계 캐시 TTL — 5~10분 사이.
_CACHE_TTL = timedelta(minutes=7)
# "최근 활동" 으로 칠 시간 창.
_ACTIVE_WINDOW = timedelta(minutes=15)

_ELEMENT_KO = {
    "wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수",
}

# 모듈 레벨 단일 캐시. (process-local — 워커별로 따로 갖지만 집계 특성상 문제없음)
_cache: dict = {"expires": None, "data": None}


async def _compute_global(db: AsyncSession) -> dict:
    """viewer 와 무관한 전역 집계. 캐시 대상."""
    now = datetime.now(timezone.utc)
    midnight = _snap_to_midnight_kst(now)
    active_since = now - _ACTIVE_WINDOW

    signups_total = int(
        (await db.execute(select(func.count(User.id)))).scalar_one() or 0
    )
    signups_today = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.created_at >= midnight)
            )
        ).scalar_one()
        or 0
    )

    # 성비
    gender_rows = (
        await db.execute(select(User.gender, func.count(User.id)).group_by(User.gender))
    ).all()
    gender = {"male": 0, "female": 0}
    for g, c in gender_rows:
        if g in gender:
            gender[g] = int(c)

    # 활성 채팅방 = 메시지가 1개 이상 오간 스레드 수
    active_chat_rooms = int(
        (
            await db.execute(select(func.count(func.distinct(Message.thread_id))))
        ).scalar_one()
        or 0
    )

    # 오늘 맺어진 인연 = 오늘 열람된 카드 수
    today_matches = int(
        (
            await db.execute(
                select(func.count(CardUnlock.id)).where(
                    CardUnlock.unlocked_at >= midnight
                )
            )
        ).scalar_one()
        or 0
    )

    # 최근 활동 사용자 근사치 = 최근 15분 내 메시지 전송 ∪ 카드 열람
    msg_users = set(
        (
            await db.execute(
                select(Message.sender_id).where(Message.created_at >= active_since)
            )
        )
        .scalars()
        .all()
    )
    unlock_users = set(
        (
            await db.execute(
                select(CardUnlock.user_id).where(
                    CardUnlock.unlocked_at >= active_since
                )
            )
        )
        .scalars()
        .all()
    )
    active_users = len(msg_users | unlock_users)

    # 전 회원 일간(日干) / 오행(五行) 분포 — 개인화 룩업용.
    users = (
        (await db.execute(select(User).where(User.birth_date.is_not(None))))
        .scalars()
        .all()
    )
    day_stem_counts: dict[str, int] = {}
    element_counts: dict[str, int] = {}
    for u in users:
        try:
            stem, _branch = _day_pillar(u.birth_date)
            day_stem_counts[stem] = day_stem_counts.get(stem, 0) + 1
        except Exception:
            pass
        try:
            dom = _dominant_element(calculate_saju(u).element_profile)
            if dom:
                element_counts[dom] = element_counts.get(dom, 0) + 1
        except Exception:
            pass

    return {
        "signups_total": signups_total,
        "signups_today": signups_today,
        "gender": gender,
        "active_chat_rooms": active_chat_rooms,
        "today_matches": today_matches,
        "active_users": active_users,
        "day_stem_counts": day_stem_counts,
        "element_counts": element_counts,
    }


async def _get_global(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    if _cache["data"] is not None and _cache["expires"] and now < _cache["expires"]:
        return _cache["data"]
    data = await _compute_global(db)
    _cache["data"] = data
    _cache["expires"] = now + _CACHE_TTL
    return data


async def home_stats(viewer: User, db: AsyncSession) -> dict:
    """전광판 데이터. 전역 집계(캐시) + viewer 개인화 수치."""
    g = await _get_global(db)

    # 개인화: 나와 같은 일간/오행 회원 수(본인 제외).
    same_day_stem = None
    same_element = None
    if viewer.birth_date is not None:
        try:
            stem, _branch = _day_pillar(viewer.birth_date)
            others = max(g["day_stem_counts"].get(stem, 0) - 1, 0)
            same_day_stem = {"stem": stem, "count": others}
        except Exception:
            pass
        try:
            dom = _dominant_element(calculate_saju(viewer).element_profile)
            if dom:
                others = max(g["element_counts"].get(dom, 0) - 1, 0)
                same_element = {"element": _ELEMENT_KO.get(dom, dom), "count": others}
        except Exception:
            pass

    return {
        "signups_total": g["signups_total"],
        "signups_today": g["signups_today"],
        "gender": g["gender"],
        "active_chat_rooms": g["active_chat_rooms"],
        "today_matches": g["today_matches"],
        "active_users": g["active_users"],
        "same_day_stem": same_day_stem,
        "same_element": same_element,
    }
