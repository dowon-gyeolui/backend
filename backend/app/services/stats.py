"""홈 전광판(스탯 티커) 데이터 — 전역 집계 + 개인화 사주 분포."""

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

_CACHE_TTL = timedelta(minutes=7)
_ACTIVE_WINDOW = timedelta(minutes=15)

_ELEMENT_KO = {
    "wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수",
}

_cache: dict = {"expires": None, "data": None}


async def _compute_global(db: AsyncSession) -> dict:
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

    gender_rows = (
        await db.execute(select(User.gender, func.count(User.id)).group_by(User.gender))
    ).all()
    gender = {"male": 0, "female": 0}
    for g, c in gender_rows:
        if g in gender:
            gender[g] = int(c)

    active_chat_rooms = int(
        (
            await db.execute(select(func.count(func.distinct(Message.thread_id))))
        ).scalar_one()
        or 0
    )

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
    g = await _get_global(db)

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