from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import _get_or_create_engine, close_database

SEEDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "seeds")

TABLES = ["observation_points", "earthquakes", "predictions"]

DATETIME_FIELDS = {
    "earthquakes": ["timestamp"],
    "predictions": ["arrival_time"],
}


def serialize_row(table: str, row: dict) -> dict:
    serialized = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, list):
            serialized[key] = json.dumps(value)
        else:
            serialized[key] = value
    return serialized


async def dump_table(engine, table: str) -> list[dict]:
    async with engine.connect() as conn:
        result = await conn.execute(text(f"SELECT * FROM {table} ORDER BY id"))
        rows = result.mappings().all()
        return [serialize_row(table, dict(row)) for row in rows]


async def main() -> None:
    os.makedirs(SEEDS_DIR, exist_ok=True)
    engine = _get_or_create_engine()
    async with engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: None)

    for table in TABLES:
        rows = await dump_table(engine, table)
        filepath = os.path.join(SEEDS_DIR, f"{table}.json")
        with open(filepath, "w") as f:
            json.dump(rows, f, indent=2)
        print(f"Dumped {len(rows)} rows to {filepath}")

    await close_database()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
