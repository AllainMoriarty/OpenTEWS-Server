from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from app.schemas.observation_point import ObservationPointRead
from app.schemas.prediction import PredictionRead


class EarthquakeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    strike: float
    dip: float
    rake: float
    slip_m: float
    rupture_length_km: float
    rupture_width_km: float
    observation_point: Optional[ObservationPointRead] = None
    predictions: list[PredictionRead] = []
