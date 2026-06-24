from app.models.base import Base
from app.models.earthquake import Earthquake
from app.models.enums import TsunamiPotential
from app.models.observation_point import ObservationPoint
from app.models.prediction import Prediction

__all__ = [
    "Base",
    "Earthquake",
    "ObservationPoint",
    "Prediction",
    "TsunamiPotential",
]
