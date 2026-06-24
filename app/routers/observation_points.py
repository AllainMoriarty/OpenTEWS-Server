from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas import ObservationPointRead
from app.services import list_observation_points

router = APIRouter(prefix="/observation-points", tags=["observation-points"])


@router.get("", response_model=list[ObservationPointRead])
async def get_all_observation_points(
    db: AsyncSession = Depends(get_db),
) -> list[ObservationPointRead]:
    return await list_observation_points(db)
