from __future__ import annotations

from app.models import ObservationPoint
from app.routers import observation_points as observation_points_router_module


def test_get_all_observation_points_returns_data(client, db_session_stub, monkeypatch) -> None:
    async def fake_list_observation_points(session):
        assert session is db_session_stub
        return [
            ObservationPoint(
                id=10,
                location_name="Pantai Selatan Jawa",
                latitude=-8.45,
                longitude=110.25,
            )
        ]

    monkeypatch.setattr(
        observation_points_router_module,
        "list_observation_points",
        fake_list_observation_points,
    )

    response = client.get("/api/observation-points")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == 10
    assert payload[0]["location_name"] == "Pantai Selatan Jawa"
    assert payload[0]["latitude"] == -8.45
    assert payload[0]["longitude"] == 110.25
