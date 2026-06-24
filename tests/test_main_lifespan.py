from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module


class _DummySessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _):
        return None


def test_lifespan_starts_cron_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            POSTGRES_DB="tews_db",
            SCRAPER_CRON_ENABLED=True,
            SCRAPER_CRON_INTERVAL_MINUTES=5,
            SCRAPER_RUN_ON_STARTUP=False,
        ),
    )
    monkeypatch.setattr(main_module, "get_session_factory", lambda: lambda: _DummySessionContext())
    monkeypatch.setattr(main_module, "get_redis", lambda: None)

    async def _noop_close() -> None:
        return None

    monkeypatch.setattr(main_module, "close_database", _noop_close)
    monkeypatch.setattr(main_module, "close_redis", _noop_close)

    with TestClient(main_module.app):
        pass


def test_lifespan_handles_cron_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            POSTGRES_DB="tews_db",
            SCRAPER_CRON_ENABLED=False,
            SCRAPER_CRON_INTERVAL_MINUTES=5,
            SCRAPER_RUN_ON_STARTUP=False,
        ),
    )
    monkeypatch.setattr(main_module, "get_session_factory", lambda: lambda: _DummySessionContext())
    monkeypatch.setattr(main_module, "get_redis", lambda: None)

    async def _noop_close() -> None:
        return None

    monkeypatch.setattr(main_module, "close_database", _noop_close)
    monkeypatch.setattr(main_module, "close_redis", _noop_close)

    with TestClient(main_module.app):
        pass
