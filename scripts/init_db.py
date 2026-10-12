"""Create the pgvector extension, all tables, and vector indexes.

Run with::

    python scripts/init_db.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from agentmesh import models  # noqa: E402, F401  (register metadata)
from agentmesh.db import Base, dispose_engine, get_engine  # noqa: E402

DEMO_ROWS = [
    ("T-1001", "Acme Facilities", "hvac", "high", "open", "2026-01-04", "Austin", 420.0),
    ("T-1002", "Bluebird Realty", "plumbing", "medium", "closed", "2026-01-06", "Dallas", 180.5),
    ("T-1003", "Cedar Grove HOA", "electrical", "high", "open", "2026-01-09", "Houston", 640.0),
    ("T-1004", "Delta Property Co", "appliance", "low", "closed", "2026-01-11", "Austin", 95.0),
    ("T-1005", "Evergreen Retail", "hvac", "critical", "open", "2026-01-12", "San Antonio", 1250.0),
    ("T-1006", "Fairview Clinics", "plumbing", "high", "pending", "2026-01-14", "Dallas", 310.0),
    ("T-1007", "Granite Offices", "electrical", "medium", "closed", "2026-01-15", "Houston", 220.0),
    ("T-1008", "Harbor Logistics", "hvac", "medium", "open", "2026-01-18", "Corpus Christi", 480.0),
    ("T-1009", "Ivy Lane Apartments", "appliance", "high", "pending", "2026-01-20", "Austin", 150.0),
    ("T-1010", "Juniper Hotels", "plumbing", "critical", "open", "2026-01-22", "San Antonio", 990.0),
]


async def _init_demo_table(conn) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id SERIAL PRIMARY KEY,
                ticket_ref TEXT NOT NULL UNIQUE,
                customer TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at DATE NOT NULL,
                city TEXT,
                amount NUMERIC(10, 2) NOT NULL DEFAULT 0
            )
            """
        )
    )
    for row in DEMO_ROWS:
        await conn.execute(
            text(
                """
                INSERT INTO support_tickets
                    (ticket_ref, customer, category, priority, status, opened_at, city, amount)
                VALUES (:ref, :customer, :category, :priority, :status, :opened, :city, :amount)
                ON CONFLICT (ticket_ref) DO NOTHING
                """
            ),
            {
                "ref": row[0],
                "customer": row[1],
                "category": row[2],
                "priority": row[3],
                "status": row[4],
                "opened": date.fromisoformat(row[5]),
                "city": row[6],
                "amount": row[7],
            },
        )


async def init_db() -> None:
    """Create the extension, tables, an HNSW index, and the demo table."""

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
                "ON chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        await _init_demo_table(conn)
    print(
        "Schema ready: extension 'vector', all tables, "
        "chunks_embedding_hnsw index, and demo support_tickets table."
    )
    await dispose_engine()


def main() -> None:
    asyncio.run(init_db())


if __name__ == "__main__":
    main()
