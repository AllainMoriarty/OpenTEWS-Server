"""Initial schema for earthquakes, observation points, and predictions.

Revision ID: 20260425_0001
Revises:
Create Date: 2026-04-25 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260425_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    tsunami_potential_enum = postgresql.ENUM(
        "NO_THREAT",
        "THREAT",
        name="tsunami_potential_enum",
    )
    tsunami_potential_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "earthquakes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("depth_km", sa.Float(), nullable=False),
        sa.Column("magnitude", sa.Float(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("dip", sa.Float(), nullable=False),
        sa.Column("rake", sa.Float(), nullable=False),
        sa.Column("slip_m", sa.Float(), nullable=False),
        sa.Column("rupture_length_km", sa.Float(), nullable=False),
        sa.Column("rupture_width_km", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_earthquakes")),
    )
    op.create_index(op.f("ix_earthquakes_timestamp"), "earthquakes", ["timestamp"], unique=False)

    op.create_table(
        "observation_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("location_name", sa.String(length=255), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observation_points")),
    )

    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("earthquake_id", sa.BigInteger(), nullable=False),
        sa.Column("observation_point_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tsunami_potential",
            postgresql.ENUM(
                "NO_THREAT",
                "THREAT",
                name="tsunami_potential_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("max_height", sa.Float(), nullable=True),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_series", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["earthquake_id"],
            ["earthquakes.id"],
            name=op.f("fk_predictions_earthquake_id_earthquakes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_point_id"],
            ["observation_points.id"],
            name=op.f("fk_predictions_observation_point_id_observation_points"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_predictions")),
        sa.UniqueConstraint(
            "earthquake_id",
            "observation_point_id",
            name="uq_predictions_earthquake_observation_point",
        ),
    )
    op.create_index("ix_predictions_earthquake_id", "predictions", ["earthquake_id"], unique=False)
    op.create_index(
        "ix_predictions_observation_point_id",
        "predictions",
        ["observation_point_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_predictions_observation_point_id", table_name="predictions")
    op.drop_index("ix_predictions_earthquake_id", table_name="predictions")
    op.drop_table("predictions")

    op.drop_table("observation_points")

    op.drop_index(op.f("ix_earthquakes_timestamp"), table_name="earthquakes")
    op.drop_table("earthquakes")

    tsunami_potential_enum = postgresql.ENUM(
        "NO_THREAT",
        "THREAT",
        name="tsunami_potential_enum",
    )
    tsunami_potential_enum.drop(op.get_bind(), checkfirst=True)
