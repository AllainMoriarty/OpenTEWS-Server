from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from fastapi import WebSocket
from redis.asyncio import Redis, from_url

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class WebSocketManager:
    """In-process WebSocket registry with optional Redis pub/sub fan-out.

    When a Redis client is provided, ``broadcast`` publishes to a Redis channel
    and a background subscriber on every worker delivers messages to its local
    connections. This lets the service run with multiple uvicorn workers while
    keeping WebSocket broadcasts consistent. If Redis is unavailable, broadcasts
    fall back to in-process delivery only.
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._redis = redis
        self._subscriber_task: asyncio.Task[None] | None = None
        self._subscriber_client: Redis | None = None

    async def start(self) -> None:
        if self._redis is None:
            return
        settings = get_settings()
        self._subscriber_client = from_url(settings.redis_url, decode_responses=True)
        self._subscriber_task = asyncio.create_task(
            self._subscriber_loop(settings.WEBSOCKET_REDIS_CHANNEL),
            name="ws-redis-subscriber",
        )

    async def stop(self) -> None:
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._subscriber_task
            self._subscriber_task = None
        if self._subscriber_client is not None:
            with suppress(Exception):
                await self._subscriber_client.aclose()
            self._subscriber_client = None

    async def _subscriber_loop(self, channel: str) -> None:
        pubsub = None
        while True:
            try:
                pubsub = self._subscriber_client.pubsub()
                await pubsub.subscribe(channel)
                logger.info("Subscribed to WebSocket redis channel: %s", channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    await self._send_to_locals(payload)
            except asyncio.CancelledError:
                if pubsub is not None:
                    with suppress(Exception):
                        await pubsub.unsubscribe(channel)
                    with suppress(Exception):
                        await pubsub.aclose()
                raise
            except Exception:
                logger.exception("WebSocket subscriber loop error; reconnecting in 2s")
                if pubsub is not None:
                    with suppress(Exception):
                        await pubsub.aclose()
                    pubsub = None
                await asyncio.sleep(2.0)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("WebSocket client connected (%s total)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("WebSocket client disconnected (%s total)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        if self._redis is None:
            await self._send_to_locals(message)
            return
        try:
            settings = get_settings()
            await self._redis.publish(settings.WEBSOCKET_REDIS_CHANNEL, json.dumps(message))
        except Exception:
            logger.warning("Redis publish failed; broadcasting locally only", exc_info=True)
            await self._send_to_locals(message)

    async def _send_to_locals(self, message: dict[str, Any]) -> None:
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._connections.discard(ws)
            if dead:
                logger.info("Cleaned %s stale WebSocket connections", len(dead))
