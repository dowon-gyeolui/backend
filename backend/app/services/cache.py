"""Redis 기반 LLM 응답 캐시 — REDIS_URL 미설정/장애 시 인메모리 폴백."""

from __future__ import annotations

import time
from typing import Optional

from app.config import settings

_client = None

_memory: dict[str, tuple[str, float]] = {}
_MEMORY_MAX_ENTRIES = 512


def _memory_get(key: str) -> Optional[str]:
    item = _memory.get(key)
    if item is None:
        return None
    value, expires_at = item
    if time.monotonic() > expires_at:
        _memory.pop(key, None)
        return None
    return value


def _memory_set(key: str, value: str, ttl_seconds: int) -> None:
    if len(_memory) >= _MEMORY_MAX_ENTRIES:
        now = time.monotonic()
        for k in [k for k, (_, exp) in _memory.items() if exp < now]:
            _memory.pop(k, None)
        if len(_memory) >= _MEMORY_MAX_ENTRIES:
            _memory.pop(next(iter(_memory)), None)
    _memory[key] = (value, time.monotonic() + ttl_seconds)


_client_failed = False


def _get_client():
    global _client, _client_failed
    if _client_failed or not settings.redis_url:
        return None
    if _client is None:
        try:
            import redis.asyncio as redis

            # 환경변수에 따옴표째 붙여넣는 실수를 보정한다.
            url = settings.redis_url.strip().strip('"').strip("'")
            _client = redis.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:
            print(
                f"[cache] Redis 클라이언트 생성 실패 — 메모리 캐시로 동작: {exc!r}",
                flush=True,
            )
            _client_failed = True
            return None
    return _client


async def cache_get(key: str) -> Optional[str]:
    client = _get_client()
    if client is not None:
        try:
            value = await client.get(key)
            if value is not None:
                return value
        except Exception:
            pass
    return _memory_get(key)


async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    _memory_set(key, value, ttl_seconds)
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(key, value, ex=ttl_seconds)
    except Exception:
        pass


def _memory_incr(key: str, ttl_seconds: int) -> int:
    now = time.monotonic()
    item = _memory.get(key)
    if item is not None:
        value, expires_at = item
        if now <= expires_at:
            new_value = int(value) + 1
            _memory[key] = (str(new_value), expires_at)
            return new_value
    if len(_memory) >= _MEMORY_MAX_ENTRIES:
        for k in [k for k, (_, exp) in _memory.items() if exp < now]:
            _memory.pop(k, None)
        if len(_memory) >= _MEMORY_MAX_ENTRIES:
            _memory.pop(next(iter(_memory)), None)
    _memory[key] = ("1", now + ttl_seconds)
    return 1


async def cache_incr(key: str, ttl_seconds: int) -> int:
    """카운터를 1 증가시키고 새 값을 반환한다 (레이트리밋용). 최초 증가 시 TTL 부여."""
    client = _get_client()
    if client is not None:
        try:
            value = await client.incr(key)
            if value == 1:
                await client.expire(key, ttl_seconds)
            return value
        except Exception:
            pass
    return _memory_incr(key, ttl_seconds)


async def close_cache() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


async def cache_pop(key: str) -> Optional[str]:
    """값을 읽고 즉시 지운다 — 1회용 로그인 코드처럼 재사용되면 안 되는 값에 쓴다.

    Redis 가 있으면 GETDEL 로 원자적으로 처리한다. 여기서 값을 못 읽었다고
    메모리 사본으로 되돌아가면 같은 코드가 두 번 통과할 수 있어, Redis 응답이
    권위 있는 답이다(장애로 예외가 난 경우에만 메모리를 본다).
    """
    client = _get_client()
    if client is not None:
        try:
            value = await client.getdel(key)
            _memory.pop(key, None)
            return value
        except Exception:
            pass
    value = _memory_get(key)
    _memory.pop(key, None)
    return value
