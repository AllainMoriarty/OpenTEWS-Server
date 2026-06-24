from __future__ import annotations

from datetime import UTC, datetime

from app.models import Earthquake
from app.routers import earthquakes as earthquakes_router_module


def test_get_all_earthquakes_returns_data(client, db_session_stub, monkeypatch) -> None:
    async def fake_list_earthquakes(session, start=None, end=None):
        assert session is db_session_stub
        return [
            Earthquake(
                id=1,
                timestamp=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
                latitude=-2.15,
                longitude=120.55,
                depth_km=20.0,
                magnitude=6.2,
                strike=180.0,
                dip=25.0,
                rake=90.0,
                slip_m=1.4,
                rupture_length_km=68.0,
                rupture_width_km=42.0,
            )
        ]

    monkeypatch.setattr(earthquakes_router_module, "list_earthquakes", fake_list_earthquakes)

    response = client.get("/api/earthquakes")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == 1
    assert payload[0]["magnitude"] == 6.2
    assert payload[0]["depth_km"] == 20.0
    assert payload[0]["timestamp"].startswith("2026-04-25T10:00:00")
