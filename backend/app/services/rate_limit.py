"""사용자별 일일 호출 횟수 제한 — app.services.cache 의 Redis/메모리 카운터 재사용."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.cache import cache_incr

_KST = timezone(timedelta(hours=9))

_DAY_TTL_S = 24 * 3600


async def check_daily_limit(user_id: int, action: str, limit: int) -> bool:
    """오늘 이 user_id/action 조합의 호출 횟수가 limit 이하이면 True (허용)."""
    today = datetime.now(_KST).date().isoformat()
    key = f"ratelimit:{user_id}:{today}:{action}"
    count = await cache_incr(key, _DAY_TTL_S)
    return count <= limit
