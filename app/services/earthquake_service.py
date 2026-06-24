from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Earthquake


async def list_earthquakes(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Earthquake]:
    query = (
        select(Earthquake)
        .options(selectinload(Earthquake.observation_point), selectinload(Earthquake.predictions))
        .order_by(Earthquake.timestamp.desc(), Earthquake.id.desc())
    )
    if start is not None:
        query = query.where(Earthquake.timestamp >= start)
    if end is not None:
        query = query.where(Earthquake.timestamp <= end)
    result = await session.scalars(query)
    return list(result.all())
