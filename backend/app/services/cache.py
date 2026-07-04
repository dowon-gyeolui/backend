"""Redis 기반 LLM 응답 캐시 — REDIS_URL 미설정/장애 시 조용히 no-op."""

from __future__ import annotations

from typing import Optional

from app.config import settings

_client = None


def _get_client():
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        import redis.asyncio as redis

        _client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


async def cache_get(key: str) -> Optional[str]:
    client = _get_client()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
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