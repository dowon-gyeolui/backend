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


async def close_cache() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None