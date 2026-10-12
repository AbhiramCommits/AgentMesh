"""Reproducible benchmark harness for AgentMesh.

Measures throughput, crash-recovery, failure-policy, retrieval, and routing
behaviour with the deterministic FakeModelProvider (seeded, offline), then writes
``results/benchmark.json`` and ``results/RESULTS.md`` and prints a summary table.

Usage::

    python scripts/benchmark.py            # full suite
    python scripts/benchmark.py --quick    # CI smoke run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Larger pool so the 32-way concurrency level is not pool-bound.
os.environ.setdefault("AGENTMESH_DB_POOL_SIZE", "60")
os.environ.setdefault("AGENTMESH_DB_MAX_OVERFLOW", "40")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from agentmesh.agents.analyst import AnalystAgent  # noqa: E402
from agentmesh.agents.base import RunContext, StepResult  # noqa: E402
from agentmesh.agents.planner import PlannerAgent  # noqa: E402
from agentmesh.agents.retrieval_agent import RetrievalAgent  # noqa: E402
from agentmesh.agents.verifier import VerifierAgent  # noqa: E402
from agentmesh.db import (  # noqa: E402
    COMMITS,
    get_engine,
    reset_commit_counter,
    session_scope,
)
from agentmesh.engine import policies  # noqa: E402
from agentmesh.engine.executor import (  # noqa: E402
    CrashInjected,
    Runtime,
    execute_run,
    resume_run,
)
from agentmesh.engine.registry import AgentRegistry, HealthTracker  # noqa: E402
from agentmesh.llm.provider import FakeModelProvider  # noqa: E402
from agentmesh.models import Run, RunStatus, Step  # noqa: E402
from agentmesh.retrieval.ingest import DOCS_DIR, ingest_directory  # noqa: E402
from agentmesh.retrieval.labels import evaluate  # noqa: E402
from agentmesh.tools import ToolContext  # noqa: E402
from agentmesh.tools.runtime import get_registry  # noqa: E402

RUN_TABLES = ["retrievals", "tool_calls", "escalations", "checkpoints", "steps", "runs"]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


async def truncate_runs() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE " + ", ".join(RUN_TABLES) + " RESTART IDENTITY CASCADE"))


async def create_run(request: str, workflow: str = "demo") -> uuid.UUID:
    async with session_scope() as session:
        run = Run(
            workflow_name=workflow,
            status=RunStatus.PENDING.value,
            input={"request": request},
            attempt_budget=3,
        )
        session.add(run)
        await session.flush()
        return run.id


async def run_status(run_id: uuid.UUID) -> str:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        return run.status if run else "missing"


async def count_steps(run_id: uuid.UUID) -> int:
    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Step).where(Step.run_id == run_id)
                )
            ).scalar_one()
        )


async def step_rows_by_node(run_id: uuid.UUID) -> dict[str, int]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Step.node_id, func.count())
                .where(Step.run_id == run_id)
                .group_by(Step.node_id)
            )
        ).all()
        return {node: int(count) for node, count in rows}


async def db_version() -> str:
    engine = get_engine()
    async with engine.connect() as conn:
        return str((await conn.execute(text("SELECT version()"))).scalar_one())


# --------------------------------------------------------------------------- #
# Custom agents/providers used to inject faults deterministically.
# --------------------------------------------------------------------------- #
class ReliableAgent:
    def __init__(self, name: str, capability: str) -> None:
        self.name = name
        self.capabilities = (capability,)

    async def run(self, ctx: RunContext) -> StepResult:
        return StepResult(output={"note": self.name}, model_used=ctx.model)


class BenchAnalyst:
    """Analyst that writes a sandbox artifact, then fails for a deterministic
    fraction of runs (forcing retries, fallbacks, and compensating actions)."""

    name = "analyst"
    capabilities = ("analyze", "revise")

    def __init__(self, rate: float, seed: int) -> None:
        self.rate = rate
        self.seed = seed

    def _will_fail(self, run_id: uuid.UUID) -> bool:
        digest = hashlib.sha1(f"{self.seed}:{run_id}".encode()).hexdigest()
        return (int(digest, 16) % 1000) < int(self.rate * 1000)

    async def run(self, ctx: RunContext) -> StepResult:
        await ctx.registry.invoke(
            "file_sandbox",
            {"op": "write", "path": f"{ctx.step_id}.txt", "content": "artifact"},
            step_id=ctx.step_id,
            ctx=ToolContext(run_id=ctx.run_id, step_id=ctx.step_id, sandbox_dir=ctx.sandbox_dir),
        )
        if self.rate and self._will_fail(ctx.run_id):
            raise RuntimeError("injected tool failure")
        return StepResult(output={"answer": "grounded answer"}, model_used=ctx.model)


class FaultProvider:
    """Wraps a provider and raises with a configured probability."""

    def __init__(self, base: FakeModelProvider, rate: float, seed: int) -> None:
        self.base = base
        self.rate = rate
        self.rng = random.Random(seed)

    async def complete(self, messages: Any, **kwargs: Any) -> Any:
        if self.rate and self.rng.random() < self.rate:
            raise RuntimeError("injected provider error")
        return await self.base.complete(messages, **kwargs)


# --------------------------------------------------------------------------- #
# Suites
# --------------------------------------------------------------------------- #
async def suite_throughput(runs_n: int, concurrencies: list[int]) -> list[dict[str, Any]]:
    runtime = Runtime.build()
    results: list[dict[str, Any]] = []
    for concurrency in concurrencies:
        before = COMMITS["count"]
        latencies: list[float] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def one(index: int) -> None:
            async with semaphore:
                run_id = await create_run(f"throughput run {index}")
                start = time.perf_counter()
                try:
                    await execute_run(run_id, runtime=runtime)
                except Exception:  # noqa: BLE001
                    pass
                latencies.append((time.perf_counter() - start) * 1000)

        wall_start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(runs_n)))
        wall = time.perf_counter() - wall_start
        results.append(
            {
                "concurrency": concurrency,
                "runs": runs_n,
                "wall_clock_s": round(wall, 3),
                "runs_per_sec": round(runs_n / wall, 2) if wall else 0.0,
                "p50_latency_ms": round(percentile(latencies, 50), 2),
                "p95_latency_ms": round(percentile(latencies, 95), 2),
                "p99_latency_ms": round(percentile(latencies, 99), 2),
                "db_commits": COMMITS["count"] - before,
            }
        )
        print(
            f"  throughput c={concurrency:<3} {results[-1]['runs_per_sec']:>8.2f} runs/s  "
            f"p95={results[-1]['p95_latency_ms']:>7.2f} ms  "
            f"commits={results[-1]['db_commits']}"
        )
    return results


async def suite_recovery(runs_n: int) -> dict[str, Any]:
    from agentmesh.engine.graph import load_graph

    graph = load_graph("demo")
    node_ids = graph.node_ids()
    runtime = Runtime.build()
    successes = 0
    reexecuted: list[int] = []
    saved_pct: list[float] = []

    for i in range(runs_n):
        crash_node = node_ids[i % len(node_ids)]
        crash_index = node_ids.index(crash_node)  # 0-based
        run_id = await create_run(f"recovery run {i}")
        try:
            await execute_run(run_id, crash_at_node=crash_node, runtime=runtime)
        except CrashInjected:
            pass
        try:
            await resume_run(run_id, runtime=runtime)
            if await run_status(run_id) == RunStatus.COMPLETED.value:
                successes += 1
        except Exception:  # noqa: BLE001
            pass

        per_node = await step_rows_by_node(run_id)
        reexecuted.append(sum(1 for count in per_node.values() if count > 1))
        saved_pct.append((crash_index / len(node_ids)) * 100.0)

    return {
        "runs": runs_n,
        "resume_success_rate": round(successes / runs_n, 4),
        "mean_nodes_reexecuted": round(sum(reexecuted) / runs_n, 4),
        "max_nodes_reexecuted": max(reexecuted),
        "mean_wasted_work_saved_pct": round(sum(saved_pct) / runs_n, 2),
    }


async def suite_failure_policies(runs_n: int, rates: list[float]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rate in rates:
        policies.reset_metrics()
        registry = AgentRegistry()
        registry.register(PlannerAgent())
        registry.register(RetrievalAgent())
        registry.register(BenchAnalyst(rate, seed=int(rate * 1000) + 7))
        registry.register(VerifierAgent())
        runtime = Runtime.build(
            provider=FaultProvider(FakeModelProvider(), rate, seed=int(rate * 1000) + 1),
            tools=get_registry(),
            health=HealthTracker(),
            agents=registry,
        )

        completed = 0
        awaiting = 0
        semaphore = asyncio.Semaphore(8)

        async def one(index: int) -> None:
            nonlocal completed, awaiting
            async with semaphore:
                run_id = await create_run(f"policy run {index} @ {rate}")
                try:
                    await execute_run(run_id, runtime=runtime)
                except Exception:  # noqa: BLE001
                    pass
                status = await run_status(run_id)
                if status == RunStatus.COMPLETED.value:
                    completed += 1
                elif status == RunStatus.AWAITING_HUMAN.value:
                    awaiting += 1

        await asyncio.gather(*(one(i) for i in range(runs_n)))
        results.append(
            {
                "injected_failure_rate": rate,
                "runs": runs_n,
                "completion_rate": round(completed / runs_n, 4),
                "escalation_rate": round(awaiting / runs_n, 4),
                "retries": policies.METRICS["retries"],
                "fallbacks": policies.METRICS["fallbacks"],
                "compensations": policies.METRICS["compensations"],
            }
        )
        print(
            f"  policies rate={rate:<4} completion={results[-1]['completion_rate']:.3f} "
            f"esc={results[-1]['escalation_rate']:.3f} retries={results[-1]['retries']} "
            f"fallbacks={results[-1]['fallbacks']} comps={results[-1]['compensations']}"
        )
    return results


async def suite_retrieval() -> dict[str, Any]:
    await ingest_directory(DOCS_DIR, reset=True)
    top1 = await evaluate(k=1)
    top5 = await evaluate(k=5)
    top5["recall_at_1"] = top1["recall_at_1"]
    top5["recall_at_1_k1"] = top1["recall_at_k"]
    return top5


async def suite_routing(before_n: int, after_n: int) -> dict[str, Any]:
    registry = AgentRegistry()
    registry.register(ReliableAgent("planner-primary", "plan"))
    registry.register(ReliableAgent("planner-backup", "plan"))
    registry.register(RetrievalAgent())
    registry.register(AnalystAgent())
    registry.register(VerifierAgent())
    runtime = Runtime.build(agents=registry)

    async def run_batch(n: int, tag: str) -> None:
        for i in range(n):
            run_id = await create_run(f"routing {tag} {i}")
            try:
                await execute_run(run_id, runtime=runtime)
            except Exception:  # noqa: BLE001
                pass

    await run_batch(before_n, "before")
    # Mark the primary planner unhealthy mid-suite.
    for _ in range(5):
        await runtime.health.record("planner-primary", ok=False)

    async with session_scope() as session:
        baseline = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Step)
                    .where(Step.node_id == "plan")
                )
            ).scalar_one()
        )

    await run_batch(after_n, "after")

    async with session_scope() as session:
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(Step).where(Step.node_id == "plan")
                )
            ).scalar_one()
        )
        after_rows = (
            await session.execute(
                select(Step.agent_name, func.count())
                .where(Step.node_id == "plan")
                .group_by(Step.agent_name)
            )
        ).all()
    total_after = total - baseline
    counts = {name: int(count) for name, count in after_rows}
    primary_after = counts.get("planner-primary", 0)
    routed_away = (total_after - primary_after) / total_after if total_after else 0.0
    return {
        "runs_before_marking": before_n,
        "runs_after_marking": after_n,
        "plan_steps_after_marking": total_after,
        "fraction_routed_away": round(routed_away, 4),
    }


def run_tests() -> dict[str, Any]:
    coverage_json = ROOT / "results" / "coverage.json"
    junit_xml = ROOT / "results" / "junit.xml"
    for path in (coverage_json, junit_xml):
        if path.exists():
            path.unlink()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=agentmesh",
            "--cov-report=json:results/coverage.json",
            "--junitxml=results/junit.xml",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    passed = 0
    skipped = 0
    if junit_xml.exists():
        import xml.etree.ElementTree as ET

        root = ET.parse(junit_xml).getroot()
        suites = root if root.tag == "testsuites" else [root]
        total = sum(int(s.get("tests", 0)) for s in suites)
        failures = sum(int(s.get("failures", 0)) for s in suites)
        errors = sum(int(s.get("errors", 0)) for s in suites)
        skipped = sum(int(s.get("skipped", 0)) for s in suites)
        passed = total - failures - errors - skipped
    coverage = 0.0
    if coverage_json.exists():
        data = json.loads(coverage_json.read_text())
        coverage = round(data.get("totals", {}).get("percent_covered", 0.0), 1)
    return {"tests_passed": passed, "tests_skipped": skipped, "coverage_pct": coverage}


def environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def render_markdown(report: dict[str, Any]) -> str:
    env = report["environment"]
    lines: list[str] = []
    lines.append("# AgentMesh Benchmark Results\n")
    lines.append(f"_Generated {env['timestamp']}._\n")
    lines.append("## Environment\n")
    lines.append(f"- Platform: {env['platform']}")
    lines.append(f"- CPU: {env['processor']} ({env['cpu_count']} cores)")
    lines.append(f"- Python: {env['python']}")
    lines.append(f"- Postgres: {report['postgres_version']}")
    lines.append("- Provider: fake (deterministic, offline)")
    lines.append(f"- Config: runs={report['config']['throughput_runs']}, "
                 f"recovery={report['config']['recovery_runs']}, "
                 f"policies={report['config']['policy_runs']}, quick={report['config']['quick']}\n")

    lines.append("## 1. Throughput\n")
    lines.append("| Concurrency | Runs | Wall (s) | Runs/sec | p50 (ms) | p95 (ms) | p99 (ms) | DB commits |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in report["throughput"]:
        lines.append(
            f"| {row['concurrency']} | {row['runs']} | {row['wall_clock_s']} | "
            f"{row['runs_per_sec']} | {row['p50_latency_ms']} | {row['p95_latency_ms']} | "
            f"{row['p99_latency_ms']} | {row['db_commits']} |"
        )
    lines.append("")

    rec = report["recovery"]
    lines.append("## 2. Crash Recovery\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Runs (crash at each node) | {rec['runs']} |")
    lines.append(f"| Resume success rate | {rec['resume_success_rate']} |")
    lines.append(f"| Mean nodes re-executed after resume | {rec['mean_nodes_reexecuted']} |")
    lines.append(f"| Max nodes re-executed | {rec['max_nodes_reexecuted']} |")
    lines.append(f"| Mean wasted work saved vs full restart | {rec['mean_wasted_work_saved_pct']}% |")
    lines.append("")

    lines.append("## 3. Failure Policies\n")
    lines.append("| Injected rate | Runs | Completion | Escalation | Retries | Fallbacks | Compensations |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in report["failure_policies"]:
        lines.append(
            f"| {row['injected_failure_rate']} | {row['runs']} | {row['completion_rate']} | "
            f"{row['escalation_rate']} | {row['retries']} | {row['fallbacks']} | "
            f"{row['compensations']} |"
        )
    lines.append("")

    ret = report["retrieval"]
    lines.append("## 4. Retrieval\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Labeled queries | {ret['n']} |")
    lines.append(f"| Recall@1 | {ret['recall_at_1']} |")
    lines.append(f"| Recall@5 | {ret['recall_at_k']} |")
    lines.append(f"| MRR | {ret['mrr']} |")
    lines.append(f"| Mean search latency (ms) | {ret['mean_latency_ms']:.3f} |")
    lines.append("")

    rout = report["routing"]
    lines.append("## 5. Health-Based Routing\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Runs before marking unhealthy | {rout['runs_before_marking']} |")
    lines.append(f"| Runs after marking unhealthy | {rout['runs_after_marking']} |")
    lines.append(f"| Fraction of plan steps routed away | {rout['fraction_routed_away']} |")
    lines.append("")

    tests = report.get("tests")
    if tests:
        lines.append("## 6. Test Suite\n")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Tests passed | {tests['tests_passed']} |")
        lines.append(f"| Tests skipped | {tests['tests_skipped']} |")
        lines.append(f"| Coverage (agentmesh, excl. api) | {tests['coverage_pct']}% |")
        lines.append("")
    return "\n".join(lines)


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== AgentMesh benchmark summary ===")
    for row in report["throughput"]:
        print(
            f"throughput c={row['concurrency']}: {row['runs_per_sec']} runs/s, "
            f"p95={row['p95_latency_ms']}ms"
        )
    rec = report["recovery"]
    print(
        f"recovery: success={rec['resume_success_rate']}, "
        f"mean_reexec={rec['mean_nodes_reexecuted']}, "
        f"saved={rec['mean_wasted_work_saved_pct']}%"
    )
    for row in report["failure_policies"]:
        print(
            f"policies rate={row['injected_failure_rate']}: completion={row['completion_rate']}, "
            f"escalation={row['escalation_rate']}, fallbacks={row['fallbacks']}, "
            f"compensations={row['compensations']}"
        )
    ret = report["retrieval"]
    print(
        f"retrieval: recall@1={ret['recall_at_1']}, recall@5={ret['recall_at_k']}, "
        f"MRR={ret['mrr']}, mean_latency={ret['mean_latency_ms']:.3f}ms"
    )
    print(f"routing: routed_away={report['routing']['fraction_routed_away']}")
    if report.get("tests"):
        print(f"tests: {report['tests']}")


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    await truncate_runs()
    reset_commit_counter()
    postgres_version = await db_version()

    throughput_runs = 20 if args.quick else 200
    recovery_runs = 20 if args.quick else 100
    policy_runs = 20 if args.quick else 60
    concurrencies = [1, 8] if args.quick else [1, 8, 32]
    rates = [0.0, 0.3] if args.quick else [0.0, 0.1, 0.3]

    print("[1/5] throughput ...")
    throughput = await suite_throughput(throughput_runs, concurrencies)
    print("[2/5] recovery ...")
    recovery = await suite_recovery(recovery_runs)
    print("[3/5] failure policies ...")
    failure_policies = await suite_failure_policies(policy_runs, rates)
    print("[4/5] retrieval ...")
    retrieval = await suite_retrieval()
    print("[5/5] routing ...")
    routing = await suite_routing(10 if args.quick else 20, 10 if args.quick else 20)

    tests = None
    if not args.quick:
        print("[tests] running pytest with coverage ...")
        tests = await asyncio.to_thread(run_tests)

    return {
        "environment": environment(),
        "postgres_version": postgres_version,
        "config": {
            "throughput_runs": throughput_runs,
            "recovery_runs": recovery_runs,
            "policy_runs": policy_runs,
            "quick": args.quick,
        },
        "throughput": throughput,
        "recovery": recovery,
        "failure_policies": failure_policies,
        "retrieval": retrieval,
        "routing": routing,
        "tests": tests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentMesh benchmark harness")
    parser.add_argument("--quick", action="store_true", help="reduced, CI-friendly run")
    args = parser.parse_args()

    report = asyncio.run(main_async(args))

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "benchmark.json").write_text(json.dumps(report, indent=2))
    (results_dir / "RESULTS.md").write_text(render_markdown(report))
    print_summary(report)
    print(f"\nWrote {results_dir / 'benchmark.json'} and {results_dir / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
