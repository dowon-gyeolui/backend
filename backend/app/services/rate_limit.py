"""사용자별 일일 호출 횟수 제한 — app.services.cache 의 Redis/메모리 카운터 재사용."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.services.cache import cache_get, cache_incr

_KST = timezone(timedelta(hours=9))

_DAY_TTL_S = 24 * 3600


def _key(subject: int | str, action: str) -> str:
    today = datetime.now(_KST).date().isoformat()
    return f"ratelimit:{subject}:{today}:{action}"


async def record_daily_attempt(subject: int | str, action: str) -> int:
    """오늘 이 subject/action 누적 횟수를 1 올리고 새 값을 반환한다."""
    return await cache_incr(_key(subject, action), _DAY_TTL_S)


async def daily_attempt_count(subject: int | str, action: str) -> int:
    """올리지 않고 오늘 누적 횟수만 읽는다.

    "실패한 시도만 센다"처럼 세는 시점과 판정하는 시점이 다른 제한에 쓴다.
    카운터가 없으면 0.
    """
    value = await cache_get(_key(subject, action))
    return int(value) if value is not None and value.isdigit() else 0


async def check_daily_limit(subject: int | str, action: str, limit: int) -> bool:
    """오늘 이 subject/action 조합의 호출 횟수가 limit 이하이면 True (허용)."""
    return await record_daily_attempt(subject, action) <= limit


def client_ip(request: Request) -> str:
    """레이트리밋 키로 쓸 요청 출처.

    운영은 프록시(Render) 뒤라 `request.client` 는 프록시 IP 다. 그래서
    `X-Forwarded-For` 의 첫 항목을 먼저 본다.

    이 헤더는 클라이언트가 위조할 수 있다 — IP 기준 제한은 아이디 목록을 훑는
    크리덴셜 스터핑의 비용을 올리는 보조 수단이고, 계정 하나를 노리는 브루트포스는
    아이디 기준 제한이 막는다. 둘을 함께 거는 이유다.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
