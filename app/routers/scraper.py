from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas import ScrapeRunSummary
from app.services import BMKGScraperService

router = APIRouter(prefix="/scraper", tags=["scraper"])


@router.post("/run", response_model=ScrapeRunSummary)
async def run_bmkg_scraper(
    limit: int | None = Query(default=None, ge=1, description="Optional max events to process"),
    db: AsyncSession = Depends(get_db),
) -> ScrapeRunSummary:
    service = BMKGScraperService()
    stats = await service.scrape_and_store(session=db, limit=limit)
    return ScrapeRunSummary(**asdict(stats))
