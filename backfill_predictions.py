"""
Backfill predictions for existing earthquakes that have none.

Usage:
    docker cp backfill_predictions.py hysea-server-server-1:/app/
    docker exec hysea-server-server-1 python backfill_predictions.py

Or locally:
    python backfill_predictions.py
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_session_factory
from app.models.earthquake import Earthquake
from app.models.prediction import Prediction
from app.models.enums import TsunamiPotential

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    session_factory = get_session_factory()

    async with session_factory() as session:
        result = await session.scalars(
            select(Earthquake)
            .outerjoin(Prediction, Prediction.earthquake_id == Earthquake.id)
            .where(Prediction.id.is_(None))
            .options(selectinload(Earthquake.observation_point))
        )
        orphans = list(result.all())

    if not orphans:
        logger.info("No orphan earthquakes found. All good!")
        return

    logger.info("Found %d earthquakes without predictions", len(orphans))

    from app.services.prediction_service import PredictionService
    from app.core.redis import get_redis

    redis = get_redis()
    try:
        svc = PredictionService(redis=redis)
    except Exception as exc:
        logger.warning(
            "PredictionService unavailable (%s); falling back to rule-based only",
            exc,
        )
        svc = None

    created = 0
    for eq in orphans:
        try:
            if svc is not None:
                prediction = await svc.predict_for_earthquake(eq)
            else:
                tsunami_potential = (
                    TsunamiPotential.THREAT
                    if eq.magnitude >= 7.5 and eq.depth_km <= 30.0
                    else TsunamiPotential.NO_THREAT
                )
                prediction = Prediction(
                    earthquake_id=eq.id,
                    tsunami_potential=tsunami_potential,
                    max_height=None,
                    arrival_time=None,
                )

            async with session_factory() as session:
                session.add(prediction)
                await session.commit()

            created += 1
            logger.info("Created prediction for earthquake id=%s M%.1f", eq.id, eq.magnitude)

        except Exception as exc:
            logger.error("Failed for earthquake id=%s: %s", eq.id, exc)

    logger.info("Done. Created %d predictions.", created)


if __name__ == "__main__":
    asyncio.run(main())
