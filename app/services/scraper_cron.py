from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bmkg_scraper_service import BMKGScraperService

logger = logging.getLogger(__name__)


class ScraperCronRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        interval_minutes: int = 5,
        run_on_startup: bool = True,
        prediction_service=None,
        ws_manager=None,
    ) -> None:
        self._session_factory = session_factory
        self._interval_seconds = max(60, int(interval_minutes) * 60)
        self._run_on_startup = run_on_startup
        self._service = BMKGScraperService(prediction_service, ws_manager)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        if self._run_on_startup:
            await self._run_once()

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

            if not stop_event.is_set():
                await self._run_once()

    async def _run_once(self) -> None:
        try:
            async with self._session_factory() as session:
                stats = await self._service.scrape_and_store(session=session)
                logger.info("Scraper cron run completed: %s", asdict(stats))
        except Exception as exc:
            logger.warning("Scraper cron run failed: %s: %s", type(exc).__name__, exc)
            logger.debug("Scraper cron traceback", exc_info=True)


async def stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
