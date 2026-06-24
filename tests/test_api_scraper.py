from __future__ import annotations

from app.services.bmkg_scraper_service import ScrapeStats


def test_run_scraper_returns_summary(client, db_session_stub, monkeypatch) -> None:
    seen: dict[str, object] = {}

    class DummyScraperService:
        def __init__(self) -> None:
            pass

        async def scrape_and_store(self, session, limit=None):
            seen["session"] = session
            seen["limit"] = limit
            return ScrapeStats(
                scanned_events=9,
                passed_realtime_filters=4,
                passed_detail_filters=3,
                inserted_earthquakes=2,
                inserted_observation_points=1,
                skipped_existing_earthquakes=1,
                skipped_errors=0,
            )

    import app.routers.scraper as scraper_router_module

    monkeypatch.setattr(scraper_router_module, "BMKGScraperService", DummyScraperService)

    response = client.post("/api/scraper/run?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scanned_events"] == 9
    assert payload["inserted_earthquakes"] == 2
    assert payload["inserted_observation_points"] == 1
    assert seen["session"] is db_session_stub
    assert seen["limit"] == 5


def test_run_scraper_rejects_invalid_limit(client) -> None:
    response = client.post("/api/scraper/run?limit=0")

    assert response.status_code == 422
