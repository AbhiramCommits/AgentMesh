"""Durable workflow executor: checkpoint-per-node state machine.

Each node execution is persisted as a ``steps`` row and, once the node
completes, the full run state is committed as a ``checkpoints`` row before the
executor advances. ``resume_run`` reloads the latest checkpoint and continues
from the next node, so a crashed run never restarts completed work.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentmesh.agents.base import Agent, RunContext, StepResult
from agentmesh.config import get_settings
from agentmesh.db import session_scope
from agentmesh.engine.graph import Node, WorkflowGraph, load_graph
from agentmesh.engine.registry import (
    AgentRegistry,
    HealthTracker,
    Supervisor,
    build_default_agent_registry,
)
from agentmesh.llm import ModelProvider, get_provider
from agentmesh.models import Checkpoint, Run, RunStatus, Step, StepStatus
from agentmesh.tools.base import ToolRegistry
from agentmesh.tools.runtime import get_registry


class CrashInjected(RuntimeError):
    """Raised by the deliberate crash injector to prove recovery works."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"crash injected at node '{node_id}'")


@dataclass
class Runtime:
    """Bundled collaborators for executing a workflow."""

    provider: ModelProvider
    tools: ToolRegistry
    agents: AgentRegistry
    health: HealthTracker
    supervisor: Supervisor
    default_model: str = "fake-model"

    @classmethod
    def build(
        cls,
        provider: ModelProvider | None = None,
        tools: ToolRegistry | None = None,
        health: HealthTracker | None = None,
        agents: AgentRegistry | None = None,
    ) -> Runtime:
        settings = get_settings()
        provider = provider or get_provider()
        tools = tools or get_registry()
        agents = agents or build_default_agent_registry()
        health = health or HealthTracker()
        return cls(
            provider=provider,
            tools=tools,
            agents=agents,
            health=health,
            supervisor=Supervisor(agents, health),
            default_model=settings.model,
        )


def _fresh_state(graph: WorkflowGraph, run_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "completed_nodes": [],
        "outputs": {},
        "iterations": {},
        "verdicts": {},
        "visited": [],
        "current_node": graph.start,
        "run_input": run_input,
    }


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _load_run(run_id: uuid.UUID) -> Run:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        return run


async def _latest_state(run_id: uuid.UUID, graph: WorkflowGraph) -> dict[str, Any]:
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Checkpoint)
                .where(Checkpoint.run_id == run_id)
                .order_by(Checkpoint.created_at.desc(), Checkpoint.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return {}
        return dict(row.state_json)


async def _set_status(run_id: uuid.UUID, status: RunStatus) -> None:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is not None:
            run.status = status.value
            run.updated_at = _utcnow()


async def _write_checkpoint(run_id: uuid.UUID, node_id: str, state: dict[str, Any]) -> None:
    async with session_scope() as session:
        session.add(Checkpoint(run_id=run_id, node_id=node_id, state_json=state))


async def _create_running_step(run_id: uuid.UUID, node: Node, attempt: int) -> uuid.UUID:
    async with session_scope() as session:
        step = Step(
            run_id=run_id,
            node_id=node.id,
            agent_name=None,
            status=StepStatus.RUNNING.value,
            attempt=attempt,
            input={"capability": node.capability},
            started_at=_utcnow(),
        )
        session.add(step)
        await session.flush()
        return step.id


async def _finish_step(
    step_id: uuid.UUID,
    *,
    agent_name: str,
    result: StepResult | None,
    error: str | None = None,
    status: StepStatus = StepStatus.COMPLETED,
) -> None:
    async with session_scope() as session:
        step = await session.get(Step, step_id)
        if step is None:
            return
        step.agent_name = agent_name
        step.status = status.value
        step.finished_at = _utcnow()
        if result is not None:
            step.output = result.output
            step.model_used = result.model_used
            step.prompt_tokens = result.prompt_tokens
            step.completion_tokens = result.completion_tokens
            step.latency_ms = result.latency_ms
        if error is not None:
            step.error = error


def _sandbox_dir(run_id: uuid.UUID) -> Path:
    settings = get_settings()
    base = Path(settings.sandbox_root) if settings.sandbox_root else Path(
        tempfile.gettempdir()
    ) / "agentmesh"
    root = base / str(run_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _run_agent(
    run_id: uuid.UUID,
    node: Node,
    state: dict[str, Any],
    runtime: Runtime,
    step_id: uuid.UUID,
    attempt: int,
) -> tuple[StepResult, str]:
    agent: Agent = await runtime.supervisor.select_agent(
        node.capability, preferred=node.agent
    )
    model = (node.models or [runtime.default_model])[0]
    ctx = RunContext(
        run_id=run_id,
        node=node,
        step_id=step_id,
        run_input=state.get("run_input", {}),
        provider=runtime.provider,
        registry=runtime.tools,
        outputs=state["outputs"],
        iterations=state["iterations"],
        attempt=attempt,
        model=model,
        models=node.models,
        sandbox_dir=_sandbox_dir(run_id),
    )
    result = await agent.run(ctx)
    if not result.model_used:
        result.model_used = model
    return result, agent.name


def _backoff_seconds(node: Node, attempt: int) -> float:
    base = node.retry.backoff_base_s
    delay = min(node.retry.backoff_max_s, base * (2 ** (attempt - 1)))
    return delay


async def _execute_node(
    run_id: uuid.UUID,
    node: Node,
    state: dict[str, Any],
    runtime: Runtime,
) -> tuple[uuid.UUID, StepResult, str, int]:
    """Run a node with bounded retries; return step_id, result, agent, attempts."""

    attempts = node.retry.max_attempts
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        step_id = await _create_running_step(run_id, node, attempt)
        try:
            result, agent_name = await _run_agent(run_id, node, state, runtime, step_id, attempt)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await _finish_step(
                step_id, agent_name="?", result=None, error=str(exc), status=StepStatus.FAILED
            )
            await runtime.health.record("?", ok=False)
            if attempt < attempts:
                await asyncio.sleep(_backoff_seconds(node, attempt))
                continue
            raise
        await _finish_step(step_id, agent_name=agent_name, result=result)
        await runtime.health.record(agent_name, ok=True, latency_ms=result.latency_ms)
        return step_id, result, agent_name, attempt
    raise last_error if last_error else RuntimeError("unreachable")


def _advance(graph: WorkflowGraph, state: dict[str, Any], node: Node, result: StepResult) -> None:
    state["visited"].append(node.id)
    state["iterations"][node.id] = state["iterations"].get(node.id, 0) + 1
    state["outputs"][node.id] = result.output
    if node.id not in state["completed_nodes"]:
        state["completed_nodes"].append(node.id)
    verdict = result.verdict
    if verdict is not None:
        state["verdicts"][node.id] = verdict
    targets = graph.successors(node.id, verdict if node.conditional else None)
    state["current_node"] = targets[0] if targets else None


async def _run_loop(
    run_id: uuid.UUID,
    graph: WorkflowGraph,
    runtime: Runtime,
    state: dict[str, Any],
    *,
    crash_at_node: str | None,
    is_resume: bool,
    executed_counter: list[int] | None = None,
) -> dict[str, Any]:
    current = state.get("current_node")
    guard = 0
    while current is not None and guard < 10_000:
        guard += 1
        node = graph.get(current)
        limit = node.loop_limit or graph.max_iterations or 1000
        if state["iterations"].get(current, 0) >= limit:
            state["current_node"] = None
            break

        if crash_at_node == current and not is_resume:
            await _create_running_step(run_id, node, attempt=1)
            if executed_counter is not None:
                executed_counter[0] += 1
            await _set_status(run_id, RunStatus.FAILED)
            raise CrashInjected(current)

        _step_id, result, _agent, _attempts = await _execute_node(run_id, node, state, runtime)
        if executed_counter is not None:
            executed_counter[0] += 1
        _advance(graph, state, node, result)
        await _write_checkpoint(run_id, node.id, state)
        current = state.get("current_node")

    return state


async def execute_run(
    run_id: uuid.UUID,
    *,
    crash_at_node: str | None = None,
    runtime: Runtime | None = None,
    executed_counter: list[int] | None = None,
) -> dict[str, Any]:
    """Execute a run from scratch (or continue if checkpoints exist)."""

    runtime = runtime or Runtime.build()
    run = await _load_run(run_id)
    graph = load_graph(run.workflow_name)

    state = await _latest_state(run_id, graph) or _fresh_state(graph, run.input)

    if run.status in {RunStatus.COMPLETED.value, RunStatus.AWAITING_HUMAN.value}:
        return state
    if state.get("current_node") is None and state.get("visited"):
        await _set_status(run_id, RunStatus.COMPLETED)
        return state

    await _set_status(run_id, RunStatus.RUNNING)
    try:
        state = await _run_loop(
            run_id,
            graph,
            runtime,
            state,
            crash_at_node=crash_at_node,
            is_resume=False,
            executed_counter=executed_counter,
        )
    except CrashInjected:
        raise
    except Exception:
        await _set_status(run_id, RunStatus.FAILED)
        raise

    if state.get("current_node") is None:
        await _set_status(run_id, RunStatus.COMPLETED)
    return state


async def resume_run(
    run_id: uuid.UUID,
    *,
    runtime: Runtime | None = None,
    crash_at_node: str | None = None,
    executed_counter: list[int] | None = None,
) -> dict[str, Any]:
    """Resume a run from its latest checkpoint without re-running nodes."""

    runtime = runtime or Runtime.build()
    run = await _load_run(run_id)
    graph = load_graph(run.workflow_name)
    state = await _latest_state(run_id, graph)
    if not state:
        state = _fresh_state(graph, run.input)
    else:
        state = dict(state)

    if state.get("current_node") is None:
        await _set_status(run_id, RunStatus.COMPLETED)
        return state

    await _set_status(run_id, RunStatus.RUNNING)
    state = await _run_loop(
        run_id,
        graph,
        runtime,
        state,
        crash_at_node=crash_at_node,
        is_resume=True,
        executed_counter=executed_counter,
    )
    if state.get("current_node") is None:
        await _set_status(run_id, RunStatus.COMPLETED)
    return state


async def count_steps(run_id: uuid.UUID) -> int:
    """Return the number of persisted step rows for a run."""

    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Step).where(Step.run_id == run_id)
                )
            ).scalar_one()
        )


async def timing_probe(coro: Any) -> tuple[Any, float]:
    """Run a coroutine and return its result with wall-clock milliseconds."""

    start = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - start) * 1000
