"""Tests for capability+health routing and the durable executor."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from agentmesh.agents.base import RunContext, StepResult
from agentmesh.db import session_scope
from agentmesh.engine.executor import (
    CrashInjected,
    Runtime,
    count_steps,
    execute_run,
    resume_run,
)
from agentmesh.engine.registry import (
    AgentRegistry,
    HealthTracker,
    NoHealthyAgent,
    Supervisor,
)
from agentmesh.llm.provider import FakeModelProvider
from agentmesh.models import Checkpoint, Run, RunStatus, Step
from agentmesh.tools import InMemoryToolRecorder, build_default_registry


class DummyAgent:
    def __init__(self, name: str, capability: str) -> None:
        self.name = name
        self.capabilities = (capability,)

    async def run(self, ctx: RunContext) -> StepResult:
        return StepResult(output={"ok": True}, model_used="dummy")


def _runtime() -> Runtime:
    recorder = InMemoryToolRecorder()
    return Runtime.build(
        provider=FakeModelProvider(),
        tools=build_default_registry(recorder=recorder),
        health=HealthTracker(),
    )


async def _create_run(request: str = "Which categories drive open tickets?") -> uuid.UUID:
    async with session_scope() as session:
        run = Run(
            workflow_name="demo",
            status=RunStatus.PENDING.value,
            input={"request": request},
            attempt_budget=3,
        )
        session.add(run)
        await session.flush()
        return run.id


async def _run_status(run_id: uuid.UUID) -> str:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run.status


@pytest.mark.integration
async def test_happy_path_completes(database: None) -> None:
    run_id = await _create_run()
    state = await execute_run(run_id, runtime=_runtime())

    assert state["current_node"] is None
    assert await _run_status(run_id) == RunStatus.COMPLETED.value
    assert await count_steps(run_id) == 4

    async with session_scope() as session:
        checkpoints = (
            await session.execute(
                select(func.count()).select_from(Checkpoint).where(Checkpoint.run_id == run_id)
            )
        ).scalar_one()
        assert checkpoints == 4

        rows = (
            await session.execute(
                select(Step.node_id, Step.status, Step.model_used).where(Step.run_id == run_id)
            )
        ).all()
    node_ids = [r[0] for r in rows]
    assert node_ids == ["plan", "retrieve", "analyze", "verify"]
    assert all(r[1] == "completed" for r in rows)


@pytest.mark.integration
async def test_crash_then_resume_no_restart(database: None) -> None:
    run_id = await _create_run()
    runtime = _runtime()
    first_counter = [0]
    with pytest.raises(CrashInjected):
        await execute_run(
            run_id, runtime=runtime, crash_at_node="analyze", executed_counter=first_counter
        )

    # 2 completed nodes + 1 crashed attempt.
    assert first_counter[0] == 3
    assert await count_steps(run_id) == 3
    assert await _run_status(run_id) == RunStatus.FAILED.value

    async with session_scope() as session:
        per_node = dict(
            (
                await session.execute(
                    select(Step.node_id, func.count())
                    .where(Step.run_id == run_id)
                    .group_by(Step.node_id)
                )
            ).all()
        )
    assert per_node == {"plan": 1, "retrieve": 1, "analyze": 1}

    resume_counter = [0]
    state = await resume_run(run_id, runtime=runtime, executed_counter=resume_counter)

    # Resume executed only analyze + verify, never plan/retrieve again.
    assert resume_counter[0] == 2
    assert state["current_node"] is None
    assert await _run_status(run_id) == RunStatus.COMPLETED.value
    assert await count_steps(run_id) == 5

    # Total interpreted executions = nodes_total + 1 (the crashed attempt).
    assert first_counter[0] + resume_counter[0] == 5

    async with session_scope() as session:
        per_node = dict(
            (
                await session.execute(
                    select(Step.node_id, func.count())
                    .where(Step.run_id == run_id)
                    .group_by(Step.node_id)
                )
            ).all()
        )
    assert per_node == {"plan": 1, "retrieve": 1, "analyze": 2, "verify": 1}


@pytest.mark.integration
async def test_resume_from_scratch_is_noop_when_complete(database: None) -> None:
    run_id = await _create_run()
    await execute_run(run_id, runtime=_runtime())
    before = await count_steps(run_id)
    await resume_run(run_id, runtime=_runtime())
    assert await count_steps(run_id) == before


async def test_supervisor_skips_unhealthy_agent() -> None:
    registry = AgentRegistry()
    registry.register(DummyAgent("agent-a", "plan"))
    registry.register(DummyAgent("agent-b", "plan"))
    health = HealthTracker(min_success_rate=0.5, min_samples=1)
    supervisor = Supervisor(registry, health)

    for _ in range(5):
        await health.record("agent-a", ok=False)

    chosen = await supervisor.select_agent("plan")
    assert chosen.name == "agent-b"


async def test_supervisor_skips_stale_heartbeat() -> None:
    registry = AgentRegistry()
    registry.register(DummyAgent("agent-a", "plan"))
    health = HealthTracker(stale_after_s=1.0)
    supervisor = Supervisor(registry, health)
    await health.heartbeat("agent-a", ("plan",))
    health.snapshot("agent-a").last_heartbeat -= 10  # type: ignore[union-attr]

    with pytest.raises(NoHealthyAgent):
        await supervisor.select_agent("plan")


async def test_supervisor_raises_when_no_capability() -> None:
    registry = AgentRegistry()
    registry.register(DummyAgent("agent-a", "plan"))
    supervisor = Supervisor(registry, HealthTracker())
    with pytest.raises(NoHealthyAgent):
        await supervisor.select_agent("nonexistent")
