from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import close_database, get_session_factory
from app.core.redis import close_redis, get_redis
from app.routers import api_router
from app.routers.ws import router as ws_router
from app.services.scraper_cron import ScraperCronRunner, stop_task
from app.services.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))

    ws_manager = WebSocketManager(redis=get_redis())
    app.state.ws_manager = ws_manager
    app.state.redis = ws_manager._redis
    await ws_manager.start()

    settings = get_settings()
    stop_event = asyncio.Event()
    cron_task: asyncio.Task[None] | None = None

    from app.services.prediction_service import PredictionService

    try:
        prediction_service = PredictionService(redis=get_redis())
    except Exception as exc:
        logger.warning(
            "PredictionService unavailable (%s: %s); scraper will run without ML predictions",
            type(exc).__name__,
            exc,
        )
        prediction_service = None

    if settings.SCRAPER_CRON_ENABLED:
        cron_runner = ScraperCronRunner(
            session_factory=session_factory,
            interval_minutes=settings.SCRAPER_CRON_INTERVAL_MINUTES,
            run_on_startup=settings.SCRAPER_RUN_ON_STARTUP,
            prediction_service=prediction_service,
            ws_manager=ws_manager,
        )
        cron_task = asyncio.create_task(cron_runner.run_forever(stop_event))
        logger.info(
            "Scraper cron enabled: interval=%s minutes",
            settings.SCRAPER_CRON_INTERVAL_MINUTES,
        )

    yield

    stop_event.set()
    if cron_task is not None:
        await stop_task(cron_task)

    await ws_manager.stop()
    await close_redis()
    await close_database()


app = FastAPI(
    title="HySEA Web API",
    version="0.1.0",
    lifespan=lifespan,
)

if get_settings().CORS_ENABLED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router)
app.include_router(ws_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "database": settings.POSTGRES_DB,
    }
