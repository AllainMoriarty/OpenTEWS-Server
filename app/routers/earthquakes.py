from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas import EarthquakeRead
from app.services import list_earthquakes
from app.models.enums import TsunamiPotential
from app.models.prediction import Prediction

router = APIRouter(prefix="/earthquakes", tags=["earthquakes"])

CSV_COLUMNS = [
    "id",
    "timestamp",
    "latitude",
    "longitude",
    "magnitude",
    "depth_km",
    "location_name",
    "tsunami_potential",
    "max_height",
    "arrival_time",
    "eta_minutes",
]


def _parse_query_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _eta_minutes(timestamp: datetime, arrival_time: datetime | None) -> int | None:
    if arrival_time is None:
        return None
    delta = arrival_time - timestamp
    seconds = delta.total_seconds()
    if seconds <= 0:
        return None
    return round(seconds / 60)


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


@router.get("", response_model=list[EarthquakeRead])
async def get_all_earthquakes(
    start: str | None = Query(None, description="ISO 8601 start (inclusive). Naive assumed UTC."),
    end: str | None = Query(None, description="ISO 8601 end (inclusive). Naive assumed UTC."),
    db: AsyncSession = Depends(get_db),
) -> list[EarthquakeRead]:
    return await list_earthquakes(db, _parse_query_dt(start), _parse_query_dt(end))


@router.get("/export")
async def export_earthquakes_csv(
    start: str | None = Query(None, description="ISO 8601 start (inclusive). Defaults to 30 days ago."),
    end: str | None = Query(None, description="ISO 8601 end (inclusive). Defaults to now."),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    start_dt = _parse_query_dt(start)
    end_dt = _parse_query_dt(end)
    if start_dt is None:
        start_dt = datetime.now(timezone.utc) - timedelta(days=30)
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)

    earthquakes = await list_earthquakes(db, start_dt, end_dt)

    def iter_rows() -> io.StringIO:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()

        for eq in earthquakes:
            prediction: Prediction | None = eq.predictions[0] if eq.predictions else None
            potential = ""
            if prediction is not None:
                potential = (
                    "THREAT"
                    if prediction.tsunami_potential == TsunamiPotential.THREAT
                    else "NO_THREAT"
                )
            writer.writerow([
                eq.id,
                _format_dt(eq.timestamp),
                eq.latitude,
                eq.longitude,
                eq.magnitude,
                eq.depth_km,
                eq.observation_point.location_name if eq.observation_point else "",
                potential,
                prediction.max_height if prediction and prediction.max_height is not None else "",
                _format_dt(prediction.arrival_time) if prediction and prediction.arrival_time else "",
                _eta_minutes(eq.timestamp, prediction.arrival_time if prediction else None) or "",
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    filename = f"earthquakes_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter_rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
