from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ObservationPoint


async def list_observation_points(session: AsyncSession) -> list[ObservationPoint]:
    query = select(ObservationPoint).order_by(
        ObservationPoint.location_name.asc(), ObservationPoint.id.asc()
    )
    result = await session.scalars(query)
    return list(result)
