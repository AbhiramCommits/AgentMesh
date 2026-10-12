"""Tests for retrieval ingestion, provenance, and quality metrics."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from agentmesh.db import session_scope
from agentmesh.models import Retrieval, Run, RunStatus, Step
from agentmesh.retrieval import chunk_text, evaluate, ingest_directory, load_labels, search
from agentmesh.retrieval.ingest import DOCS_DIR


def test_chunk_text_overlaps_and_bounds() -> None:
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_words=300, overlap_words=60)
    assert len(chunks) >= 3
    assert len(chunks[0].split()) == 300
    # overlapping tail of chunk 0 equals head of chunk 1
    assert chunks[0].split()[-60:] == chunks[1].split()[:60]


@pytest.mark.integration
async def test_recall_metrics(database: None) -> None:
    await ingest_directory(DOCS_DIR, reset=True)
    metrics = await evaluate(k=5)
    assert metrics["n"] >= 30
    assert metrics["recall_at_1"] >= 0.9
    assert metrics["recall_at_k"] >= 0.98
    assert metrics["mrr"] >= 0.9
    assert metrics["mean_latency_ms"] >= 0.0


@pytest.mark.integration
async def test_top1_hits_on_sample_queries(database: None) -> None:
    await ingest_directory(DOCS_DIR, reset=True)
    labels = load_labels()[:5]
    for item in labels:
        hits = await search(item["query"], k=5)
        assert hits, f"no hits for {item['query']!r}"
        assert item["expected"].lower() in hits[0].title.lower()
        assert hits[0].score > 0.0


@pytest.mark.integration
async def test_retrieval_provenance_recorded(database: None) -> None:
    from agentmesh.engine.executor import Runtime, execute_run
    from agentmesh.tools import InMemoryToolRecorder, build_default_registry

    await ingest_directory(DOCS_DIR, reset=True)

    async with session_scope() as session:
        run = Run(
            workflow_name="demo",
            status=RunStatus.PENDING.value,
            input={"request": "What are the response time targets for critical tickets?"},
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    runtime = Runtime.build(tools=build_default_registry(recorder=InMemoryToolRecorder()))
    await execute_run(run_id, runtime=runtime)

    async with session_scope() as session:
        retrieve_step = (
            await session.execute(
                select(Step).where(Step.run_id == run_id, Step.node_id == "retrieve")
            )
        ).scalar_one()
        count = (
            await session.execute(
                select(func.count())
                .select_from(Retrieval)
                .where(Retrieval.step_id == retrieve_step.id)
            )
        ).scalar_one()
        assert count >= 1
        sample = (
            await session.execute(
                select(Retrieval).where(Retrieval.step_id == retrieve_step.id).limit(1)
            )
        ).scalar_one()
        assert sample.chunk_text
        assert sample.doc_id is not None
        assert isinstance(uuid.UUID(str(sample.doc_id)), uuid.UUID)
