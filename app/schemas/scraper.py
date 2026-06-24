from __future__ import annotations

from pydantic import BaseModel


class ScrapeRunSummary(BaseModel):
    scanned_events: int
    passed_realtime_filters: int
    passed_detail_filters: int
    inserted_earthquakes: int
    inserted_observation_points: int
    skipped_existing_earthquakes: int
    skipped_errors: int
