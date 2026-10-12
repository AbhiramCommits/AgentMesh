"""Tests for bounded retries, fallback models, compensation, and escalation."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from agentmesh.agents.base import RunContext, StepResult
from agentmesh.agents.planner import PlannerAgent
from agentmesh.agents.retrieval_agent import RetrievalAgent
from agentmesh.agents.verifier import VerifierAgent
from agentmesh.db import session_scope
from agentmesh.engine.executor import Runtime, execute_run
from agentmesh.engine.graph import Node, RetryPolicy
from agentmesh.engine.policies import (
    NodeFailed,
    RunPaused,
    compensate_step,
    compute_backoff,
    execute_node_with_recovery,
    resolve_escalation,
)
from agentmesh.engine.registry import AgentRegistry, HealthTracker
from agentmesh.llm.provider import FakeModelProvider
from agentmesh.models import (
    Escalation,
    EscalationStatus,
    Run,
    RunStatus,
    Step,
    StepStatus,
    ToolCall,
)
from agentmesh.tools import InMemoryToolRecorder, build_default_registry


class BoomAgent:
    name = "boom"
    capabilities = ("boom",)

    async def run(self, ctx: RunContext) -> StepResult:
        raise RuntimeError("provider exploded")


class ModelAwareAgent:
    name = "model-aware"
    capabilities = ("m",)

    def __init__(self, fail_models: set[str]) -> None:
        self.fail_models = fail_models
        self.seen: list[str] = []

    async def run(self, ctx: RunContext) -> StepResult:
        self.seen.append(ctx.model)
        if ctx.model in self.fail_models:
            raise RuntimeError(f"model {ctx.model} unavailable")
        return StepResult(output={"answer": "ok"}, model_used=ctx.model)


class VerdictAgent:
    name = "verdict"
    capabilities = ("v",)

    async def run(self, ctx: RunContext) -> StepResult:
        if ctx.model == "m1":
            return StepResult(
                output={"verdict": "fail"}, verdict="fail", model_used="m1", latency_ms=1
            )
        return StepResult(
            output={"verdict": "pass"}, verdict="pass", model_used="m2", latency_ms=1
        )


class FailingAnalyst:
    name = "analyst"
    capabilities = ("analyze", "revise")

    async def run(self, ctx: RunContext) -> StepResult:
        raise RuntimeError("analysis tool timeout")


def _runtime(agents: AgentRegistry) -> Runtime:
    return Runtime.build(
        provider=FakeModelProvider(),
        tools=build_default_registry(recorder=InMemoryToolRecorder()),
        health=HealthTracker(),
        agents=agents,
    )


async def _create_run(workflow: str = "demo") -> uuid.UUID:
    async with session_scope() as session:
        run = Run(
            workflow_name=workflow,
            status=RunStatus.PENDING.value,
            input={"request": "Which categories drive open tickets?"},
        )
        session.add(run)
        await session.flush()
        return run.id


async def _count_steps(run_id: uuid.UUID, node_id: str) -> int:
    from sqlalchemy import func

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Step)
                    .where(Step.run_id == run_id, Step.node_id == node_id)
                )
            ).scalar_one()
        )


def test_compute_backoff_is_bounded() -> None:
    node = Node(
        id="n",
        capability="c",
        retry=RetryPolicy(max_attempts=5, backoff_base_s=0.1, backoff_max_s=0.5, jitter=0.5),
    )
    for attempt in range(1, 6):
        delay = compute_backoff(node, attempt)
        assert 0.0 <= delay <= node.retry.backoff_max_s * 1.5 + 1e-9


@pytest.mark.integration
async def test_retry_count_honored_and_backoff_bounded(database: None) -> None:
    run_id = await _create_run()
    registry = AgentRegistry()
    registry.register(BoomAgent())
    node = Node(
        id="boom-node",
        capability="boom",
        retry=RetryPolicy(max_attempts=3, backoff_base_s=0.01, backoff_max_s=0.05, jitter=0.0),
        on_failure="fail",
    )
    state = {
        "outputs": {},
        "iterations": {},
        "run_input": {},
        "completed_nodes": [],
        "visited": [],
        "verdicts": {},
    }
    started = time.perf_counter()
    with pytest.raises(NodeFailed):
        await execute_node_with_recovery(run_id, node, state, _runtime(registry))
    elapsed = time.perf_counter() - started
    assert await _count_steps(run_id, "boom-node") == 3
    assert elapsed < 1.0


@pytest.mark.integration
async def test_fallback_model_used_after_primary_fails(database: None) -> None:
    run_id = await _create_run()
    registry = AgentRegistry()
    registry.register(ModelAwareAgent(fail_models={"m1"}))
    node = Node(
        id="fallback",
        capability="m",
        models=["m1", "m2"],
        retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01, jitter=0.0),
        on_failure="fail",
    )
    state = {
        "outputs": {},
        "iterations": {},
        "run_input": {},
        "completed_nodes": [],
        "visited": [],
        "verdicts": {},
    }
    outcome = await execute_node_with_recovery(run_id, node, state, _runtime(registry))
    assert outcome.attempts == 2
    assert outcome.result.model_used == "m2"

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Step).where(Step.run_id == run_id).order_by(Step.attempt)
            )
        ).scalars().all()
    assert [r.status for r in rows] == ["failed", "completed"]
    assert rows[-1].model_used == "m2"


@pytest.mark.integration
async def test_verifier_fail_triggers_fallback(database: None) -> None:
    run_id = await _create_run()
    registry = AgentRegistry()
    registry.register(VerdictAgent())
    node = Node(
        id="verify-node",
        capability="v",
        models=["m1", "m2"],
        retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01, jitter=0.0),
        on_failure="fail",
    )
    state = {
        "outputs": {},
        "iterations": {},
        "run_input": {},
        "completed_nodes": [],
        "visited": [],
        "verdicts": {},
    }
    outcome = await execute_node_with_recovery(run_id, node, state, _runtime(registry))
    assert outcome.attempts == 2
    assert outcome.result.verdict == "pass"
    assert outcome.result.model_used == "m2"


@pytest.mark.integration
async def test_compensation_deletes_sandbox_artifact(database: None) -> None:
    run_id = await _create_run()
    from agentmesh.engine.store import sandbox_dir

    async with session_scope() as session:
        step = Step(
            run_id=run_id,
            node_id="analyze",
            status=StepStatus.FAILED.value,
            attempt=1,
        )
        session.add(step)
        await session.flush()
        step_id = step.id
        session.add(
            ToolCall(
                step_id=step_id,
                tool_name="file_sandbox",
                args={"op": "write", "path": "artifact.txt", "content": "temp"},
                result={"op": "write", "path": "artifact.txt", "bytes": 4},
                ok=True,
            )
        )

    artifact = sandbox_dir(run_id) / "artifact.txt"
    artifact.write_text("temp", encoding="utf-8")
    assert artifact.exists()

    runtime = _runtime(AgentRegistry())
    removed = await compensate_step(run_id, "analyze", runtime)
    assert removed == 1
    assert not artifact.exists()


@pytest.mark.integration
async def test_repeated_failure_escalates_then_resolve_completes(database: None) -> None:
    run_id = await _create_run()
    registry = AgentRegistry()
    registry.register(PlannerAgent())
    registry.register(RetrievalAgent())
    registry.register(FailingAnalyst())
    registry.register(VerifierAgent())
    runtime = _runtime(registry)

    with pytest.raises(RunPaused):
        await execute_run(run_id, runtime=runtime)

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.status == RunStatus.AWAITING_HUMAN.value
        escalation = (
            await session.execute(select(Escalation).where(Escalation.run_id == run_id))
        ).scalar_one()
        assert escalation.status == EscalationStatus.OPEN.value
        escalation_id = escalation.id

    await resolve_escalation(escalation_id, "Approved by on-call manager", runtime=runtime)

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        escalation = await session.get(Escalation, escalation_id)
        assert run is not None and run.status == RunStatus.COMPLETED.value
        assert escalation is not None
        assert escalation.status == EscalationStatus.RESOLVED.value
        assert escalation.resolution == "Approved by on-call manager"
