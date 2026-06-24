"""Move observation point FK from predictions to earthquakes.

Revision ID: 20260427_0002
Revises: 20260425_0001
Create Date: 2026-04-27 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260427_0002"
down_revision = "20260425_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("earthquakes", sa.Column("observation_point_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_earthquakes_observation_point_id",
        "earthquakes",
        ["observation_point_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_earthquakes_observation_point_id_observation_points"),
        "earthquakes",
        "observation_points",
        ["observation_point_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill earthquakes.observation_point_id from existing predictions rows.
    op.execute(
        sa.text(
            """
            UPDATE earthquakes AS e
            SET observation_point_id = p.observation_point_id
            FROM (
                SELECT earthquake_id, MIN(observation_point_id) AS observation_point_id
                FROM predictions
                WHERE observation_point_id IS NOT NULL
                GROUP BY earthquake_id
            ) AS p
            WHERE e.id = p.earthquake_id
            """
        )
    )

    op.drop_constraint(
        "uq_predictions_earthquake_observation_point",
        "predictions",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_predictions_observation_point_id_observation_points"),
        "predictions",
        type_="foreignkey",
    )
    op.drop_index("ix_predictions_observation_point_id", table_name="predictions")
    op.drop_column("predictions", "observation_point_id")


def downgrade() -> None:
    op.add_column("predictions", sa.Column("observation_point_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_predictions_observation_point_id",
        "predictions",
        ["observation_point_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_predictions_observation_point_id_observation_points"),
        "predictions",
        "observation_points",
        ["observation_point_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Restore prediction observation references from earthquakes table.
    op.execute(
        sa.text(
            """
            UPDATE predictions AS p
            SET observation_point_id = e.observation_point_id
            FROM earthquakes AS e
            WHERE p.earthquake_id = e.id
            """
        )
    )

    op.alter_column("predictions", "observation_point_id", nullable=False)
    op.create_unique_constraint(
        "uq_predictions_earthquake_observation_point",
        "predictions",
        ["earthquake_id", "observation_point_id"],
    )

    op.drop_constraint(
        op.f("fk_earthquakes_observation_point_id_observation_points"),
        "earthquakes",
        type_="foreignkey",
    )
    op.drop_index("ix_earthquakes_observation_point_id", table_name="earthquakes")
    op.drop_column("earthquakes", "observation_point_id")
