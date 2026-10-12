"""Async SQLAlchemy engine, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from agentmesh.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all AgentMesh ORM models."""


# Lightweight transaction counter used by the benchmark harness.
COMMITS: dict[str, int] = {"count": 0}


def reset_commit_counter() -> None:
    COMMITS["count"] = 0


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it lazily."""

    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""

    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope yielding an :class:`AsyncSession`."""

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
            COMMITS["count"] += 1
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a session and commits on success."""

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
            COMMITS["count"] += 1
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the engine and reset cached globals (used in tests/shutdown)."""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def reset_engine_for_tests(url: str | None = None) -> None:
    """Reset the cached engine, optionally overriding the URL (test helper)."""

    global _engine, _session_factory
    _engine = None
    _session_factory = None
    if url is not None:
        settings = get_settings()
        settings.database_url = url

