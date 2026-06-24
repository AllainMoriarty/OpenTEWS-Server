from fastapi import APIRouter

from app.routers.earthquakes import router as earthquakes_router
from app.routers.observation_points import router as observation_points_router
from app.routers.scraper import router as scraper_router

api_router = APIRouter(prefix="/api")
api_router.include_router(earthquakes_router)
api_router.include_router(observation_points_router)
api_router.include_router(scraper_router)
