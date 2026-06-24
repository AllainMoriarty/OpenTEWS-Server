from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.services.bmkg_scraper_service import BMKGScraperService


class _FakeSession:
    def __init__(self, scalar_result=None):
        self.scalar_result = scalar_result
        self.last_query = None

    async def scalar(self, query):
        self.last_query = query
        return self.scalar_result

    def add(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None


def test_find_existing_earthquake_uses_tolerance_window() -> None:
    marker = object()
    session = _FakeSession(scalar_result=marker)
    service = BMKGScraperService()

    duplicate = asyncio.run(
        service._find_existing_earthquake(
            session=session,
            timestamp=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
            latitude=-3.2,
            longitude=120.0,
            magnitude=6.0,
        )
    )

    assert duplicate is marker
    compiled = str(session.last_query)
    assert "abs(earthquakes.latitude -" in compiled
    assert "abs(earthquakes.longitude -" in compiled
    assert "abs(earthquakes.magnitude -" in compiled
    assert "earthquakes.timestamp >=" in compiled
    assert "earthquakes.timestamp <=" in compiled


def test_observation_point_name_dedup_is_case_insensitive() -> None:
    existing_marker = object()
    session = _FakeSession(scalar_result=existing_marker)
    service = BMKGScraperService()

    found, created = asyncio.run(
        service._get_or_create_observation_point(
            session=session,
            location_name="  laut banda  ",
            latitude=-4.2,
            longitude=129.7,
        )
    )

    assert found is existing_marker
    assert created is False
    compiled = str(session.last_query)
    assert "lower(trim(observation_points.location_name))" in compiled
