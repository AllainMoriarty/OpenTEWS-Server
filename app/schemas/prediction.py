from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from app.models.enums import TsunamiPotential


class PredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    earthquake_id: int
    tsunami_potential: TsunamiPotential
    max_height: float | None = None
    arrival_time: datetime | None = None
    eta_series: list[float] | None = None
