"""Retrieval agent: pgvector search plus chunk-provenance recording."""

from __future__ import annotations

import time
import uuid

from agentmesh.agents.base import RunContext, StepResult
from agentmesh.db import session_scope
from agentmesh.models import Retrieval
from agentmesh.retrieval.search import search


class RetrievalAgent:
    """Grounds the workflow in the document store and records provenance."""

    name = "retriever"
    capabilities: tuple[str, ...] = ("retrieve",)

    async def run(self, ctx: RunContext) -> StepResult:
        start = time.perf_counter()
        request = str(ctx.run_input.get("request", "")).strip()
        query = str(ctx.node.params.get("query") or request)
        k = int(ctx.node.params.get("k", 5))
        hits = await search(query, k=k)

        async with session_scope() as session:
            for hit in hits:
                try:
                    doc_id = uuid.UUID(hit.doc_id)
                    chunk_id = uuid.UUID(hit.chunk_id)
                except (ValueError, TypeError):
                    continue
                session.add(
                    Retrieval(
                        step_id=ctx.step_id,
                        doc_id=doc_id,
                        chunk_id=chunk_id,
                        chunk_text=hit.content,
                        score=hit.score,
                    )
                )

        latency_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            output={
                "query": query,
                "evidence": [hit.to_dict() for hit in hits],
                "count": len(hits),
            },
            model_used="embedding:fake",
            latency_ms=latency_ms,
        )
