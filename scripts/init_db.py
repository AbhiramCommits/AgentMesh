"""Create the pgvector extension, all tables, and vector indexes.

Run with::

    python scripts/init_db.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from agentmesh import models  # noqa: E402, F401  (register metadata)
from agentmesh.db import Base, dispose_engine, get_engine  # noqa: E402


async def init_db() -> None:
    """Create the extension, tables, and an HNSW index on chunk embeddings."""

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
    print("Schema ready: extension 'vector', all tables, and chunks_embedding_hnsw index.")
    await dispose_engine()


def main() -> None:
    asyncio.run(init_db())


if __name__ == "__main__":
    main()
