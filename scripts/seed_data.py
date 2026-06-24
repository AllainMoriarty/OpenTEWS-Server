from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import _get_or_create_engine, close_database

SEEDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "seeds")

TABLES_IN_ORDER = [
    "observation_points",
    "earthquakes",
    "predictions",
]

DATETIME_FIELDS = {
    "earthquakes": ["timestamp"],
    "predictions": ["arrival_time"],
}

JSONB_FIELDS = {
    "predictions": ["eta_series"],
}


def deserialize_row(table: str, row: dict) -> dict:
    deserialized = {}
    for key, value in row.items():
        if key in DATETIME_FIELDS.get(table, []) and value is not None:
            deserialized[key] = datetime.fromisoformat(value)
        elif key in JSONB_FIELDS.get(table, []) and value is not None:
            deserialized[key] = json.loads(value) if isinstance(value, str) else value
        else:
            deserialized[key] = value
    return deserialized


async def load_seed_file(table: str) -> list[dict]:
    filepath = os.path.join(SEEDS_DIR, f"{table}.json")
    if not os.path.exists(filepath):
        print(f"  Skipping {table}: seed file not found at {filepath}")
        return []
    with open(filepath) as f:
        rows = json.load(f)
    return [deserialize_row(table, row) for row in rows]


async def truncate_tables(engine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        for table in reversed(TABLES_IN_ORDER):
            await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        await conn.commit()
    print("Truncated all tables.")


async def insert_rows(engine, table: str, rows: list[dict]) -> None:
    if not rows:
        return

    columns = list(rows[0].keys())
    col_names = ", ".join(columns)
    placeholders = ", ".join(f":{col}" for col in columns)
    stmt = text(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})")

    async with engine.connect() as conn:
        for row in rows:
            await conn.execute(stmt, row)
        max_id = max(r["id"] for r in rows)
        await conn.execute(
            text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), :max_id)"),
            {"max_id": max_id},
        )
        await conn.commit()
    print(f"  Inserted {len(rows)} rows into {table}")


async def main() -> None:
    engine = _get_or_create_engine()

    print("Truncating existing data...")
    await truncate_tables(engine)

    for table in TABLES_IN_ORDER:
        print(f"Loading {table}...")
        rows = await load_seed_file(table)
        await insert_rows(engine, table, rows)

    await close_database()
    print("Done.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
