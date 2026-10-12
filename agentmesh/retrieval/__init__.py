"""Retrieval: pgvector ingestion and search with chunk provenance."""

from __future__ import annotations

from agentmesh.retrieval.ingest import (
    chunk_text,
    ingest_directory,
    ingest_text,
    reset_corpus,
)
from agentmesh.retrieval.labels import evaluate, load_labels
from agentmesh.retrieval.search import SearchHit, search

__all__ = [
    "SearchHit",
    "chunk_text",
    "evaluate",
    "ingest_directory",
    "ingest_text",
    "load_labels",
    "reset_corpus",
    "search",
]
