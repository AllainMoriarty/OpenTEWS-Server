from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Float, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.earthquake import Earthquake


class ObservationPoint(Base):
    __tablename__ = "observation_points"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    location_name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    earthquakes: Mapped[list[Earthquake]] = relationship(back_populates="observation_point")
