"""Failure-recovery policies: retries, fallback models, compensation, escalation."""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from agentmesh.agents.base import Agent, RunContext, StepResult
from agentmesh.config import get_settings
from agentmesh.db import session_scope
from agentmesh.engine import store as st
from agentmesh.engine.graph import Node, WorkflowGraph, load_graph
from agentmesh.engine.runtime import Runtime
from agentmesh.models import (
    Escalation,
    EscalationStatus,
    RunStatus,
    Step,
    StepStatus,
    ToolCall,
)
from agentmesh.tools.base import ToolContext


class NodeFailed(RuntimeError):
    """Raised when a node exhausts its recovery budget and is configured to fail."""

    def __init__(self, node_id: str, reason: str) -> None:
        self.node_id = node_id
        self.reason = reason
        super().__init__(reason)


class RunPaused(Exception):
    """Raised when a node escalates to a human and the run stops cleanly."""

    def __init__(self, run_id: uuid.UUID, node_id: str, escalation_id: uuid.UUID) -> None:
        self.run_id = run_id
        self.node_id = node_id
        self.escalation_id = escalation_id
        super().__init__(f"run {run_id} paused at node '{node_id}'")


@dataclass
class NodeOutcome:
    """Result of executing one node with recovery policies applied."""

    step_id: uuid.UUID
    result: StepResult
    agent_name: str
    attempts: int


def compute_backoff(node: Node, attempt: int, rng: random.Random | None = None) -> float:
    """Exponential backoff with bounded jitter for the given attempt number."""

    delay = min(node.retry.backoff_max_s, node.retry.backoff_base_s * (2 ** (attempt - 1)))
    if rng is not None:
        jitter = rng.uniform(0.0, node.retry.jitter * delay)
    else:
        jitter = random.uniform(0.0, node.retry.jitter * delay)
    return delay + jitter


def build_context(
    run_id: uuid.UUID,
    node: Node,
    state: dict[str, Any],
    runtime: Runtime,
    step_id: uuid.UUID,
    attempt: int,
    model: str,
) -> RunContext:
    return RunContext(
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
        sandbox_dir=st.sandbox_dir(run_id),
    )


async def execute_node_with_recovery(
    run_id: uuid.UUID,
    node: Node,
    state: dict[str, Any],
    runtime: Runtime,
) -> NodeOutcome:
    """Execute a node with retries, fallback models, compensation, and escalation."""

    models = node.models or [runtime.default_model]
    max_attempts = node.retry.max_attempts
    last_error: Exception | None = None
    last_step_id: uuid.UUID | None = None

    for attempt in range(1, max_attempts + 1):
        model = models[min(attempt - 1, len(models) - 1)]
        agent: Agent = await runtime.supervisor.select_agent(
            node.capability, preferred=node.agent
        )
        step_id = await st.create_running_step(run_id, node, attempt)
        last_step_id = step_id
        ctx = build_context(run_id, node, state, runtime, step_id, attempt, model)

        try:
            result = await agent.run(ctx)
        except Exception as exc:  # noqa: BLE001 - provider/tool/agent failure
            last_error = exc
            await st.finish_step(
                step_id,
                agent_name=agent.name,
                result=None,
                error=str(exc),
                status=StepStatus.FAILED,
            )
            await runtime.health.record(agent.name, ok=False)
            if attempt < max_attempts:
                await asyncio.sleep(compute_backoff(node, attempt))
                continue
            break

        if not result.model_used:
            result.model_used = model

        if result.verdict == "fail" and attempt < max_attempts:
            await st.finish_step(
                step_id,
                agent_name=agent.name,
                result=result,
                error="verifier returned fail; retrying with fallback model",
                status=StepStatus.FAILED,
            )
            await runtime.health.record(agent.name, ok=False, latency_ms=result.latency_ms)
            await asyncio.sleep(compute_backoff(node, attempt))
            continue

        await st.finish_step(step_id, agent_name=agent.name, result=result)
        await runtime.health.record(agent.name, ok=True, latency_ms=result.latency_ms)
        return NodeOutcome(step_id, result, agent.name, attempt)

    await compensate_step(run_id, node.id, runtime)
    reason = (
        f"node '{node.id}' failed after {max_attempts} attempts: "
        f"{type(last_error).__name__ if last_error else 'verifier fail'}"
        f"{': ' + str(last_error) if last_error else ''}"
    )
    if node.on_failure == "escalate":
        escalation_id = await escalate_run(run_id, node.id, last_step_id, reason)
        raise RunPaused(run_id, node.id, escalation_id)
    raise NodeFailed(node.id, reason)



async def compensate_step(run_id: uuid.UUID, node_id: str, runtime: Runtime) -> int:
    """Run compensating actions for successful non-idempotent calls, in reverse."""

    async with session_scope() as session:
        step_ids = (
            await session.execute(
                select(Step.id).where(Step.run_id == run_id, Step.node_id == node_id)
            )
        ).scalars().all()
        if not step_ids:
            return 0
        rows = (
            await session.execute(
                select(ToolCall)
                .where(ToolCall.step_id.in_(step_ids), ToolCall.ok.is_(True))
                .order_by(ToolCall.created_at.desc(), ToolCall.id.desc())
            )
        ).scalars().all()
        pending = [
            (row.tool_name, dict(row.args or {}), row.result, row.step_id) for row in rows
        ]

    compensated = 0
    for tool_name, args, result, step_id in pending:
        if not runtime.tools.has(tool_name):
            continue
        spec = runtime.tools.get(tool_name)
        if spec.idempotent or spec.compensate is None:
            continue
        ctx = ToolContext(run_id=run_id, step_id=step_id, sandbox_dir=st.sandbox_dir(run_id))
        await runtime.tools.compensate(tool_name, args, result, ctx)
        compensated += 1
    return compensated


async def escalate_run(
    run_id: uuid.UUID,
    node_id: str,
    step_id: uuid.UUID | None,
    reason: str,
) -> uuid.UUID:
    """Create an escalation row, queue it in Redis, and pause the run."""

    async with session_scope() as session:
        escalation = Escalation(
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            status=EscalationStatus.OPEN.value,
        )
        session.add(escalation)
        await session.flush()
        escalation_id = escalation.id

    await st.set_status(run_id, RunStatus.AWAITING_HUMAN)
    await _queue_escalation(escalation_id, run_id, node_id, reason)
    return escalation_id


async def _queue_escalation(
    escalation_id: uuid.UUID, run_id: uuid.UUID, node_id: str, reason: str
) -> None:
    try:
        import json

        import redis.asyncio as aioredis

        client = aioredis.from_url(get_settings().redis_url)
        await client.rpush(
            "agentmesh:escalations",
            json.dumps(
                {
                    "escalation_id": str(escalation_id),
                    "run_id": str(run_id),
                    "node_id": node_id,
                    "reason": reason,
                }
            ),
        )
        await client.aclose()
    except Exception:  # noqa: BLE001 - Redis is best effort
        pass


async def resolve_escalation(
    escalation_id: uuid.UUID,
    resolution: str,
    runtime: Runtime | None = None,
) -> uuid.UUID:
    """Resolve an escalation and resume the run from its checkpoint."""

    async with session_scope() as session:
        escalation = await session.get(Escalation, escalation_id)
        if escalation is None:
            raise ValueError(f"escalation {escalation_id} not found")
        escalation.status = EscalationStatus.RESOLVED.value
        escalation.resolution = resolution
        run_id = escalation.run_id
        step = await session.get(Step, escalation.step_id) if escalation.step_id else None
        node_id = step.node_id if step else None

    run = await st.load_run(run_id)
    graph = load_graph(run.workflow_name)
    state = await st.latest_state(run_id, graph)
    if not state:
        state = st.fresh_state(graph, run.input)

    if node_id is not None and graph.has(node_id):
        node = graph.get(node_id)
        _apply_human_resolution(graph, state, node, resolution)
        await st.write_checkpoint(run_id, node_id, state)

    from agentmesh.engine.executor import resume_run

    await resume_run(run_id, runtime=runtime)
    return run_id


def _apply_human_resolution(
    graph: WorkflowGraph,
    state: dict[str, Any],
    node: Node,
    resolution: str,
) -> None:
    state["visited"].append(node.id)
    state["iterations"][node.id] = state["iterations"].get(node.id, 0) + 1
    state["outputs"][node.id] = {"human_resolution": resolution, "escalated": True}
    if node.id not in state["completed_nodes"]:
        state["completed_nodes"].append(node.id)
    verdict = "pass" if node.conditional else None
    targets = graph.successors(node.id, verdict)
    state["current_node"] = targets[0] if targets else None
