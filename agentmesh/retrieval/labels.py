"""Labeled retrieval queries and an evaluator for recall/MRR metrics."""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentmesh.retrieval.search import search

ROOT = Path(__file__).resolve().parents[2]
LABELS_PATH = ROOT / "data" / "labeled_queries.json"


def load_labels(path: str | Path | None = None) -> list[dict[str, str]]:
    """Load the labeled (query, expected title) evaluation set."""

    target = Path(path) if path else LABELS_PATH
    return json.loads(target.read_text(encoding="utf-8"))


async def evaluate(k: int = 5, labels: list[dict[str, str]] | None = None) -> dict:
    """Compute recall@1, recall@k, MRR, and mean search latency."""

    labels = labels or load_labels()
    hit1 = 0
    hit_k = 0
    reciprocal = 0.0
    latencies: list[float] = []

    for item in labels:
        query = item["query"]
        expected = item["expected"].lower()
        start = time.perf_counter()
        hits = await search(query, k=k)
        latencies.append((time.perf_counter() - start) * 1000)
        rank = None
        for index, hit in enumerate(hits):
            if expected in hit.title.lower():
                rank = index + 1
                break
        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= k:
            hit_k += 1
        if rank is not None:
            reciprocal += 1.0 / rank

    n = len(labels) or 1
    return {
        "n": len(labels),
        "k": k,
        "recall_at_1": hit1 / n,
        "recall_at_k": hit_k / n,
        "mrr": reciprocal / n,
        "mean_latency_ms": sum(latencies) / n if latencies else 0.0,
    }
