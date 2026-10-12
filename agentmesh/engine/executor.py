"""Durable workflow executor: checkpoint-per-node state machine.

Each node execution is persisted as a ``steps`` row and, once the node
completes, the full run state is committed as a ``checkpoints`` row before the
executor advances. ``resume_run`` reloads the latest checkpoint and continues
from the next node, so a crashed run never restarts completed work.

Node execution is delegated to :mod:`agentmesh.engine.policies`, which applies
bounded retries, fallback model routing, compensating actions, and escalation.
"""

from __future__ import annotations

import uuid
from typing import Any

from agentmesh.agents.base import StepResult
from agentmesh.engine import policies
from agentmesh.engine import store as st
from agentmesh.engine.graph import WorkflowGraph, load_graph
from agentmesh.engine.policies import NodeFailed, RunPaused
from agentmesh.engine.runtime import Runtime
from agentmesh.engine.store import count_steps
from agentmesh.models import RunStatus


class CrashInjected(RuntimeError):
    """Raised by the deliberate crash injector to prove recovery works."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"crash injected at node '{node_id}'")


def _advance(graph: WorkflowGraph, state: dict[str, Any], node: Any, result: StepResult) -> None:
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
            await st.create_running_step(run_id, node, attempt=1)
            if executed_counter is not None:
                executed_counter[0] += 1
            await st.set_status(run_id, RunStatus.FAILED)
            raise CrashInjected(current)

        outcome = await policies.execute_node_with_recovery(run_id, node, state, runtime)
        if executed_counter is not None:
            executed_counter[0] += 1
        _advance(graph, state, node, outcome.result)
        await st.write_checkpoint(run_id, node.id, state)
        current = state.get("current_node")

    return state


async def _prepare(
    run_id: uuid.UUID, runtime: Runtime
) -> tuple[WorkflowGraph, dict[str, Any], Any]:
    run = await st.load_run(run_id)
    graph = load_graph(run.workflow_name)
    state = await st.latest_state(run_id, graph)
    return graph, state, run


async def execute_run(
    run_id: uuid.UUID,
    *,
    crash_at_node: str | None = None,
    runtime: Runtime | None = None,
    executed_counter: list[int] | None = None,
) -> dict[str, Any]:
    """Execute a run from scratch (or continue if checkpoints already exist)."""

    runtime = runtime or Runtime.build()
    graph, state, run = await _prepare(run_id, runtime)
    if not state:
        state = st.fresh_state(graph, run.input)

    if run.status in {RunStatus.COMPLETED.value, RunStatus.AWAITING_HUMAN.value}:
        return state
    if state.get("current_node") is None and state.get("visited"):
        await st.set_status(run_id, RunStatus.COMPLETED)
        return state

    await st.set_status(run_id, RunStatus.RUNNING)
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
    except (CrashInjected, RunPaused):
        raise
    except NodeFailed:
        await st.set_status(run_id, RunStatus.FAILED)
        raise
    except Exception:
        await st.set_status(run_id, RunStatus.FAILED)
        raise

    if state.get("current_node") is None:
        await st.set_status(run_id, RunStatus.COMPLETED)
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
    graph, state, run = await _prepare(run_id, runtime)
    if not state:
        state = st.fresh_state(graph, run.input)
    else:
        state = dict(state)

    if state.get("current_node") is None:
        await st.set_status(run_id, RunStatus.COMPLETED)
        return state

    await st.set_status(run_id, RunStatus.RUNNING)
    try:
        state = await _run_loop(
            run_id,
            graph,
            runtime,
            state,
            crash_at_node=crash_at_node,
            is_resume=True,
            executed_counter=executed_counter,
        )
    except (CrashInjected, RunPaused):
        raise
    except NodeFailed:
        await st.set_status(run_id, RunStatus.FAILED)
        raise
    except Exception:
        await st.set_status(run_id, RunStatus.FAILED)
        raise

    if state.get("current_node") is None:
        await st.set_status(run_id, RunStatus.COMPLETED)
    return state


__all__ = [
    "CrashInjected",
    "Runtime",
    "count_steps",
    "execute_run",
    "resume_run",
]
