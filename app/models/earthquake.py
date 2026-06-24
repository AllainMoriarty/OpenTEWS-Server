from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.observation_point import ObservationPoint
    from app.models.prediction import Prediction


class Earthquake(Base):
    __tablename__ = "earthquakes"
    __table_args__ = (Index("ix_earthquakes_observation_point_id", "observation_point_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_point_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("observation_points.id", ondelete="SET NULL"),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    depth_km: Mapped[float] = mapped_column(Float, nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    dip: Mapped[float] = mapped_column(Float, nullable=False)
    rake: Mapped[float] = mapped_column(Float, nullable=False)
    slip_m: Mapped[float] = mapped_column(Float, nullable=False)
    rupture_length_km: Mapped[float] = mapped_column(Float, nullable=False)
    rupture_width_km: Mapped[float] = mapped_column(Float, nullable=False)

    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="earthquake",
        cascade="all, delete-orphan",
    )
    observation_point: Mapped[ObservationPoint | None] = relationship(back_populates="earthquakes")
