"""Ingest the seed document corpus into pgvector.

Run with::

    python scripts/seed_docs.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentmesh.retrieval.ingest import DOCS_DIR, ingest_directory  # noqa: E402


async def main() -> None:
    stats = await ingest_directory(DOCS_DIR, reset=True)
    print(f"Ingested {stats['documents']} documents ({stats['chunks']} chunks).")


if __name__ == "__main__":
    asyncio.run(main())
