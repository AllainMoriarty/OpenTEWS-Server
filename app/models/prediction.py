from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import TsunamiPotential

if TYPE_CHECKING:
    from app.models.earthquake import Earthquake


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_earthquake_id", "earthquake_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    earthquake_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("earthquakes.id", ondelete="CASCADE"),
        nullable=False,
    )
    tsunami_potential: Mapped[TsunamiPotential] = mapped_column(
        Enum(TsunamiPotential, name="tsunami_potential_enum"),
        nullable=False,
    )
    max_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    arrival_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eta_series: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)

    earthquake: Mapped[Earthquake] = relationship(back_populates="predictions")
