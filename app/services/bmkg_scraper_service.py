from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import numpy as np
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Earthquake, ObservationPoint

REALTIME_XML_URL = "https://bmkg-content-inatews.storage.googleapis.com/live30event.xml"
MT_URL_TEMPLATE = "https://bmkg-content-inatews.storage.googleapis.com/mt.{eventid}.txt"

STATUS_ALLOWED = "confirmed"
MIN_REALTIME_MAGNITUDE = 5.0
EARTHQUAKE_COORD_TOLERANCE = 0.05
EARTHQUAKE_MAGNITUDE_TOLERANCE = 0.1
EARTHQUAKE_TIME_TOLERANCE = timedelta(minutes=2)


@dataclass(slots=True)
class RealtimeEventCandidate:
    name: str
    day: str
    detail_url: str
    location_name: str
    status: str
    timestamp: datetime | None
    latitude: float | None
    longitude: float | None
    depth_km: float | None
    magnitude: float | None


@dataclass(slots=True)
class NodalPlane:
    strike: float
    dip: float
    rake: float


@dataclass(slots=True)
class ScrapeStats:
    scanned_events: int = 0
    passed_realtime_filters: int = 0
    passed_detail_filters: int = 0
    inserted_earthquakes: int = 0
    inserted_observation_points: int = 0
    skipped_existing_earthquakes: int = 0
    skipped_errors: int = 0


logger = logging.getLogger(__name__)


class BMKGScraperService:
    def __init__(self, prediction_service=None, ws_manager=None) -> None:
        self.prediction_service = prediction_service
        self.ws_manager = ws_manager

    async def scrape_and_store(
        self, session: AsyncSession, limit: int | None = None
    ) -> ScrapeStats:
        stats = ScrapeStats()

        xml = await self._fetch_url_text_async(REALTIME_XML_URL)
        candidates = self._parse_realtime_candidates(xml)
        stats.scanned_events = len(candidates)
        logger.info("Scraped %s events from XML feed", len(candidates))

        filtered_candidates = [c for c in candidates if self._passes_realtime_filters(c)]
        stats.passed_realtime_filters = len(filtered_candidates)
        logger.info(
            "Filters: %s passed (mag>=5, confirmed, bounds, depth<=70)",
            len(filtered_candidates),
        )

        if limit is not None and limit > 0:
            filtered_candidates = filtered_candidates[:limit]

        for candidate in filtered_candidates:
            try:
                np1, np2 = await self._fetch_nodal_planes(candidate.name)
                chosen_plane = self._choose_nodal_plane(np1, np2)
                if chosen_plane is None:
                    chosen_plane = self._estimate_nodal_plane(candidate)

                source_params = get_source_parameters(candidate.magnitude)
                if not self._passes_rupture_filter(source_params):
                    continue

                stats.passed_detail_filters += 1

                existing_earthquake = await self._find_existing_earthquake(
                    session=session,
                    timestamp=candidate.timestamp,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                    magnitude=candidate.magnitude,
                )
                if existing_earthquake is not None:
                    stats.skipped_existing_earthquakes += 1
                    continue

                (
                    observation_point,
                    created_observation,
                ) = await self._get_or_create_observation_point(
                    session=session,
                    location_name=candidate.location_name,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                )
                if created_observation:
                    stats.inserted_observation_points += 1

                earthquake = Earthquake(
                    observation_point_id=observation_point.id,
                    timestamp=candidate.timestamp,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                    depth_km=candidate.depth_km,
                    magnitude=candidate.magnitude,
                    strike=chosen_plane.strike,
                    dip=chosen_plane.dip,
                    rake=chosen_plane.rake,
                    slip_m=source_params["slip_m"],
                    rupture_length_km=source_params["rupture_length_km"],
                    rupture_width_km=source_params["rupture_width_km"],
                )
                session.add(earthquake)
                await session.flush()

                prediction = None
                if self.prediction_service is not None:
                    prediction = await self.prediction_service.predict_for_earthquake(earthquake)
                    session.add(prediction)

                await session.commit()
                stats.inserted_earthquakes += 1
                logger.info(
                    "Inserted earthquake id=%s M%.1f %s",
                    earthquake.id,
                    earthquake.magnitude,
                    observation_point.location_name,
                )

                await self._broadcast_earthquake(
                    earthquake,
                    observation_point,
                    prediction if self.prediction_service is not None else None,
                )
            except Exception as exc:
                logger.warning(
                    "Skipping candidate %s: %s: %s", candidate.name, type(exc).__name__, exc
                )
                await session.rollback()
                stats.skipped_errors += 1

        logger.info(
            "Scrape complete: inserted=%s skipped_existing=%s errors=%s",
            stats.inserted_earthquakes,
            stats.skipped_existing_earthquakes,
            stats.skipped_errors,
        )
        return stats

    async def _broadcast_earthquake(
        self,
        earthquake: Earthquake,
        observation_point: ObservationPoint,
        prediction: object | None,
    ) -> None:
        if self.ws_manager is None:
            return

        pred_data = None
        if prediction is not None:
            pred_data = {
                "id": prediction.id,
                "earthquake_id": prediction.earthquake_id,
                "tsunami_potential": prediction.tsunami_potential.value,
                "max_height": prediction.max_height,
                "arrival_time": prediction.arrival_time.isoformat()
                if prediction.arrival_time
                else None,
                "eta_series": prediction.eta_series,
            }

        message = {
            "type": "new_earthquake",
            "earthquake": {
                "id": earthquake.id,
                "timestamp": earthquake.timestamp.isoformat(),
                "latitude": earthquake.latitude,
                "longitude": earthquake.longitude,
                "depth_km": earthquake.depth_km,
                "magnitude": earthquake.magnitude,
                "strike": earthquake.strike,
                "dip": earthquake.dip,
                "rake": earthquake.rake,
                "slip_m": earthquake.slip_m,
                "rupture_length_km": earthquake.rupture_length_km,
                "rupture_width_km": earthquake.rupture_width_km,
                "observation_point": {
                    "id": observation_point.id,
                    "location_name": observation_point.location_name,
                    "latitude": observation_point.latitude,
                    "longitude": observation_point.longitude,
                },
                "predictions": [pred_data] if pred_data else [],
            },
        }
        await self.ws_manager.broadcast(message)

    async def _fetch_url_text_async(self, url: str) -> str:
        return await asyncio.to_thread(self._fetch_url_text, url)

    def _fetch_url_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                )
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (OSError, URLError) as exc:
            raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc

    async def _fetch_mt_text(self, event_id: str) -> str | None:
        url = MT_URL_TEMPLATE.format(eventid=event_id)
        try:
            return await self._fetch_url_text_async(url)
        except RuntimeError:
            return None

    async def _fetch_nodal_planes(
        self, event_id: str
    ) -> tuple[NodalPlane | None, NodalPlane | None]:
        mt_text = await self._fetch_mt_text(event_id)
        if not mt_text:
            return None, None
        return self._extract_nodal_planes(mt_text)

    def _parse_realtime_candidates(self, xml: str) -> list[RealtimeEventCandidate]:
        if not xml.strip():
            return []

        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return []

        candidates: list[RealtimeEventCandidate] = []
        for item in root.findall(".//gempa"):
            name = self._xml_child_text(item, "eventid")
            if not name:
                continue

            location_name = self._xml_child_text(item, "area") or "Unknown coastal location"
            status = self._xml_child_text(item, "status")
            timestamp = self._parse_datetime(self._xml_child_text(item, "waktu"))
            latitude = self._extract_first_float(self._xml_child_text(item, "lintang"))
            longitude = self._extract_first_float(self._xml_child_text(item, "bujur"))
            depth_km = self._extract_first_float(self._xml_child_text(item, "dalam"))
            magnitude = self._extract_first_float(self._xml_child_text(item, "mag"))

            candidates.append(
                RealtimeEventCandidate(
                    name=name,
                    day="",
                    detail_url="",
                    location_name=location_name,
                    status=status,
                    timestamp=timestamp,
                    latitude=latitude,
                    longitude=longitude,
                    depth_km=depth_km,
                    magnitude=magnitude,
                )
            )

        return candidates

    def _extract_nodal_planes(
        self, detail_text: str
    ) -> tuple[NodalPlane | None, NodalPlane | None]:
        np1_pattern = re.compile(
            r"(?:np1|nodal\s*plane\s*1).*?strike\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
            r".*?dip\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
            r".*?rake\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
            re.IGNORECASE | re.DOTALL,
        )
        np2_pattern = re.compile(
            r"(?:np2|nodal\s*plane\s*2).*?strike\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
            r".*?dip\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
            r".*?rake\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
            re.IGNORECASE | re.DOTALL,
        )

        np1 = self._nodal_from_match(np1_pattern.search(detail_text))
        np2 = self._nodal_from_match(np2_pattern.search(detail_text))

        if np1 is not None and np2 is not None:
            return np1, np2

        triples = re.findall(
            r"strike\s*[:=]?\s*(-?\d+(?:\.\d+)?)\D+dip\s*[:=]?\s*(-?\d+(?:\.\d+)?)\D+"
            r"rake\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
            detail_text,
            flags=re.IGNORECASE,
        )
        fallback_planes = [NodalPlane(float(s), float(d), float(r)) for s, d, r in triples[:2]]

        if np1 is None and fallback_planes:
            np1 = fallback_planes[0]
        if np2 is None and len(fallback_planes) > 1:
            np2 = fallback_planes[1]

        return np1, np2

    def _choose_nodal_plane(
        self, np1: NodalPlane | None, np2: NodalPlane | None
    ) -> NodalPlane | None:
        candidates = [
            np
            for np in (np1, np2)
            if np is not None and np.dip <= 30.0 and 70.0 <= np.rake <= 110.0
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (abs(item.rake - 90.0), item.dip))[0]

    def _estimate_nodal_plane(self, candidate: RealtimeEventCandidate) -> NodalPlane:
        strike = 0.0
        if candidate.latitude is not None and candidate.longitude is not None:
            strike = self._estimate_trench_parallel_strike(candidate.latitude, candidate.longitude)
        return NodalPlane(strike=strike, dip=20.0, rake=90.0)

    def _estimate_trench_parallel_strike(self, latitude: float, longitude: float) -> float:
        if longitude < 106.0:
            return 315.0
        if longitude < 118.0 and latitude < -6.0:
            return 285.0
        if longitude < 126.0:
            return 250.0
        return 20.0

    def _passes_realtime_filters(self, candidate: RealtimeEventCandidate) -> bool:
        if candidate.magnitude is None or candidate.magnitude < MIN_REALTIME_MAGNITUDE:
            return False
        if candidate.status.strip().lower() != STATUS_ALLOWED:
            return False
        if candidate.latitude is None or candidate.longitude is None or candidate.depth_km is None:
            return False
        if not (94.0 <= candidate.longitude <= 142.5):
            return False
        if not (-11.45 <= candidate.latitude <= 6.5):
            return False
        if not (0.0 <= candidate.depth_km <= 70.0):
            return False
        return True

    def _passes_rupture_filter(self, source_params: dict[str, float]) -> bool:
        return (
            source_params["rupture_length_km"] > 0.0
            and source_params["rupture_width_km"] > 0.0
            and source_params["slip_m"] > 0.0
            and source_params["rupture_length_km"] <= 1300.0
            and source_params["rupture_width_km"] <= 220.0
            and source_params["slip_m"] <= 50.0
        )

    async def _find_existing_earthquake(
        self,
        session: AsyncSession,
        timestamp: datetime,
        latitude: float,
        longitude: float,
        magnitude: float,
    ) -> Earthquake | None:
        time_from = timestamp - EARTHQUAKE_TIME_TOLERANCE
        time_to = timestamp + EARTHQUAKE_TIME_TOLERANCE
        query = select(Earthquake).where(
            and_(
                Earthquake.timestamp >= time_from,
                Earthquake.timestamp <= time_to,
                func.abs(Earthquake.latitude - latitude) <= EARTHQUAKE_COORD_TOLERANCE,
                func.abs(Earthquake.longitude - longitude) <= EARTHQUAKE_COORD_TOLERANCE,
                func.abs(Earthquake.magnitude - magnitude) <= EARTHQUAKE_MAGNITUDE_TOLERANCE,
            )
        )
        return await session.scalar(query)

    async def _get_or_create_observation_point(
        self,
        session: AsyncSession,
        location_name: str,
        latitude: float,
        longitude: float,
    ) -> tuple[ObservationPoint, bool]:
        normalized_name = self._normalize_space(location_name)
        query = select(ObservationPoint).where(
            func.lower(func.trim(ObservationPoint.location_name)) == normalized_name.casefold()
        )
        existing = await session.scalar(query)
        if existing is not None:
            return existing, False

        observation_point = ObservationPoint(
            location_name=normalized_name,
            latitude=latitude,
            longitude=longitude,
        )
        session.add(observation_point)
        await session.flush()
        return observation_point, True

    def _xml_child_text(self, item: ElementTree.Element, tag_name: str) -> str:
        child = item.find(tag_name)
        return self._normalize_space(child.text or "") if child is not None else ""

    def _nodal_from_match(self, match: re.Match[str] | None) -> NodalPlane | None:
        if not match:
            return None
        return NodalPlane(
            strike=float(match.group(1)),
            dip=float(match.group(2)),
            rake=float(match.group(3)),
        )

    def _extract_first_float(self, text: str) -> float | None:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None

    def _parse_datetime(self, raw_value: str) -> datetime | None:
        value = self._normalize_space(raw_value)
        if not value:
            return None

        timezone_shift = timedelta(0)
        upper = value.upper()
        if "WIB" in upper:
            timezone_shift = timedelta(hours=7)
            value = re.sub(r"\bWIB\b", "", value, flags=re.IGNORECASE)
        elif "WITA" in upper:
            timezone_shift = timedelta(hours=8)
            value = re.sub(r"\bWITA\b", "", value, flags=re.IGNORECASE)
        elif "WIT" in upper:
            timezone_shift = timedelta(hours=9)
            value = re.sub(r"\bWIT\b", "", value, flags=re.IGNORECASE)
        else:
            value = re.sub(r"\bUTC\b", "", value, flags=re.IGNORECASE)

        value = self._normalize_space(value)

        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S.%f",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        )
        for fmt in formats:
            try:
                dt = datetime.strptime(value, fmt).replace(tzinfo=UTC)
                return dt - timezone_shift
            except ValueError:
                continue

        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC) - timezone_shift
        except ValueError:
            return None

    def _normalize_space(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_key(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.casefold())


def get_source_parameters(mw: float) -> dict[str, float]:
    log_l = -2.37 + 0.57 * mw
    l_km = float(np.power(10.0, log_l))

    log_w = -1.86 + 0.46 * mw
    w_km = float(np.power(10.0, log_w))

    m0 = float(np.power(10.0, 1.5 * mw + 9.1))
    mu = 3.0e10

    area_m2 = (l_km * 1000.0) * (w_km * 1000.0)
    slip_m = m0 / (mu * area_m2)

    return {
        "rupture_length_km": round(l_km, 4),
        "rupture_width_km": round(w_km, 4),
        "slip_m": round(slip_m, 4),
    }
