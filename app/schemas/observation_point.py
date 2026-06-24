from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ObservationPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location_name: str
    latitude: float
    longitude: float
