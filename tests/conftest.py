"""Shared pytest fixtures.

Integration tests require Postgres (with pgvector) and Redis. When those are not
reachable they skip cleanly instead of failing, so the unit/engine suite always
runs offline.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from agentmesh.db import get_engine

TABLES = [
    "retrievals",
    "tool_calls",
    "escalations",
    "checkpoints",
    "steps",
    "runs",
    "chunks",
    "documents",
]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires live postgres/redis")


async def _postgres_reachable() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture
async def database() -> None:
    """Skip unless Postgres is reachable, then truncate all tables."""

    if not await _postgres_reachable():
        pytest.skip("Postgres not reachable; integration test skipped")
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        )
    yield
    from agentmesh.db import dispose_engine

    await dispose_engine()


@pytest.fixture
async def redis_required() -> None:
    """Skip unless Redis is reachable, then flush the test db."""

    try:
        import redis.asyncio as aioredis
    except Exception:  # pragma: no cover
        pytest.skip("redis package missing")

    from agentmesh.config import get_settings

    client = aioredis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis not reachable; integration test skipped")
    await client.flushdb()
    await client.aclose()
