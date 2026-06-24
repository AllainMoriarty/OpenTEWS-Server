from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.bmkg_scraper_service import (
    BMKGScraperService,
    NodalPlane,
    RealtimeEventCandidate,
    get_source_parameters,
)


def _service() -> BMKGScraperService:
    return BMKGScraperService()


def test_get_source_parameters_returns_expected_values() -> None:
    result = get_source_parameters(7.0)

    assert result["rupture_length_km"] == 41.6869
    assert result["rupture_width_km"] == 22.9087
    assert result["slip_m"] == pytest.approx(1.3896, abs=1e-4)


def test_choose_nodal_plane_prefers_valid_plane_closest_to_thrust() -> None:
    service = _service()
    np1 = NodalPlane(strike=190.0, dip=28.0, rake=74.0)
    np2 = NodalPlane(strike=15.0, dip=22.0, rake=92.0)

    chosen = service._choose_nodal_plane(np1, np2)

    assert chosen is not None
    assert chosen.strike == 15.0
    assert chosen.dip == 22.0
    assert chosen.rake == 92.0


def test_choose_nodal_plane_returns_none_when_both_fail_filter() -> None:
    service = _service()
    np1 = NodalPlane(strike=100.0, dip=45.0, rake=90.0)
    np2 = NodalPlane(strike=210.0, dip=15.0, rake=40.0)

    chosen = service._choose_nodal_plane(np1, np2)

    assert chosen is None


def test_estimate_nodal_plane_uses_thrust_fallback_when_no_mechanism() -> None:
    service = _service()
    candidate = RealtimeEventCandidate(
        name="bmg2026fallback",
        day="",
        detail_url="",
        location_name="Near South Coast of Java",
        status="Confirmed",
        timestamp=datetime(2026, 5, 3, 15, 39, tzinfo=UTC),
        latitude=-8.2,
        longitude=110.4,
        depth_km=15.0,
        magnitude=5.7,
    )

    chosen = service._estimate_nodal_plane(candidate)

    assert chosen.strike == 285.0
    assert chosen.dip == 20.0
    assert chosen.rake == 90.0


def test_realtime_filters_accept_valid_confirmed_coastal_event() -> None:
    service = _service()
    candidate = RealtimeEventCandidate(
        name="bmg2026xyz",
        day="",
        detail_url="",
        location_name="Laut Banda",
        status="Confirmed",
        timestamp=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
        latitude=-4.2,
        longitude=121.7,
        depth_km=24.0,
        magnitude=6.1,
    )

    assert service._passes_realtime_filters(candidate) is True


def test_realtime_filters_reject_non_coastal_or_wrong_status() -> None:
    service = _service()
    candidate = RealtimeEventCandidate(
        name="bmg2026bad",
        day="",
        detail_url="",
        location_name="Jakarta Selatan",
        status="Automatic",
        timestamp=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
        latitude=-6.2,
        longitude=106.8,
        depth_km=12.0,
        magnitude=6.0,
    )

    assert service._passes_realtime_filters(candidate) is False


def test_parse_realtime_candidates_from_xml_feed() -> None:
    service = _service()
    xml = """
    <Infogempa>
      <gempa>
        <eventid>bmg2026ipzh</eventid>
        <status>confirmed</status>
        <waktu>2026/05/03  14:05:23.981</waktu>
        <lintang>-9.06</lintang>
        <bujur>113.00</bujur>
        <dalam>22</dalam>
        <mag>5.8</mag>
        <fokal>undetermined</fokal>
        <area>South of Java, Indonesia</area>
      </gempa>
    </Infogempa>
    """

    candidates = service._parse_realtime_candidates(xml)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "bmg2026ipzh"
    assert candidate.location_name == "South of Java, Indonesia"
    assert candidate.status == "confirmed"
    assert candidate.timestamp == datetime(2026, 5, 3, 14, 5, 23, 981000, tzinfo=UTC)
    assert candidate.latitude == -9.06
    assert candidate.longitude == 113.0
    assert candidate.depth_km == 22.0
    assert candidate.magnitude == 5.8


def test_extract_nodal_planes_from_mt_text() -> None:
    service = _service()
    detail_text = (
        "Best Double Couple: NP1:Strike=80.0 Dip=50.7 Rake=33.9 "
        "NP2:Strike=327.0 Dip=64.4 Rake=135.4"
    )

    np1, np2 = service._extract_nodal_planes(detail_text)

    assert np1 is not None
    assert np2 is not None
    assert np1.strike == 80.0
    assert np1.dip == 50.7
    assert np1.rake == 33.9
    assert np2.strike == 327.0
    assert np2.dip == 64.4
    assert np2.rake == 135.4


def test_extract_nodal_planes_from_detail_page_text() -> None:
    service = _service()
    detail_text = (
        "Nodal Plane 1 Strike: 210 Dip: 24 Rake: 88\nNodal Plane 2 Strike: 35 Dip: 66 Rake: 112"
    )

    np1, np2 = service._extract_nodal_planes(detail_text)

    assert np1 is not None
    assert np2 is not None
    assert np1.strike == 210.0
    assert np1.dip == 24.0
    assert np1.rake == 88.0
    assert np2.strike == 35.0
    assert np2.dip == 66.0
    assert np2.rake == 112.0


def test_parse_datetime_converts_wib_to_utc() -> None:
    service = _service()

    parsed = service._parse_datetime("2026-04-25 12:30:00 WIB")

    assert parsed is not None
    assert parsed == datetime(2026, 4, 25, 5, 30, tzinfo=UTC)


def test_parse_realtime_candidates_returns_empty_on_empty_xml() -> None:
    service = _service()

    candidates = service._parse_realtime_candidates("")

    assert candidates == []


def test_parse_realtime_candidates_returns_empty_on_bad_xml() -> None:
    service = _service()

    candidates = service._parse_realtime_candidates("not xml")

    assert candidates == []
