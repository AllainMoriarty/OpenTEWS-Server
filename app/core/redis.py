from __future__ import annotations

import logging

from redis.asyncio import Redis, from_url

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Redis | None = None


def get_redis() -> Redis | None:
    settings = get_settings()
    if not settings.REDIS_ENABLED:
        return None
    global _client
    if _client is None:
        _client = from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis client initialized for %s", settings.redis_url)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            logger.warning("Error closing redis client", exc_info=True)
        _client = None
