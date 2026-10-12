"""Low-level durable-state helpers shared by the executor and policies.

Kept separate from the executor so that failure-recovery policies can persist
steps, checkpoints, and status transitions without importing the executor.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from agentmesh.agents.base import StepResult
from agentmesh.config import get_settings
from agentmesh.db import session_scope
from agentmesh.engine.graph import Node, WorkflowGraph
from agentmesh.models import Checkpoint, Run, RunStatus, Step, StepStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def fresh_state(graph: WorkflowGraph, run_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "completed_nodes": [],
        "outputs": {},
        "iterations": {},
        "verdicts": {},
        "visited": [],
        "current_node": graph.start,
        "run_input": run_input,
    }


async def load_run(run_id: uuid.UUID) -> Run:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        session.expunge(run)
        return run


async def latest_state(run_id: uuid.UUID, graph: WorkflowGraph) -> dict[str, Any]:
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


async def set_status(run_id: uuid.UUID, status: RunStatus) -> None:
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is not None:
            run.status = status.value
            run.updated_at = utcnow()


async def write_checkpoint(run_id: uuid.UUID, node_id: str, state: dict[str, Any]) -> None:
    async with session_scope() as session:
        session.add(Checkpoint(run_id=run_id, node_id=node_id, state_json=state))


async def create_running_step(run_id: uuid.UUID, node: Node, attempt: int) -> uuid.UUID:
    async with session_scope() as session:
        step = Step(
            run_id=run_id,
            node_id=node.id,
            agent_name=None,
            status=StepStatus.RUNNING.value,
            attempt=attempt,
            input={"capability": node.capability, "attempt": attempt},
            started_at=utcnow(),
        )
        session.add(step)
        await session.flush()
        return step.id


async def finish_step(
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
        step.finished_at = utcnow()
        if result is not None:
            step.output = result.output
            step.model_used = result.model_used
            step.prompt_tokens = result.prompt_tokens
            step.completion_tokens = result.completion_tokens
            step.latency_ms = result.latency_ms
        if error is not None:
            step.error = error


def sandbox_dir(run_id: uuid.UUID) -> Path:
    settings = get_settings()
    base = (
        Path(settings.sandbox_root)
        if settings.sandbox_root
        else Path(tempfile.gettempdir()) / "agentmesh"
    )
    root = base / str(run_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def count_steps(run_id: uuid.UUID) -> int:
    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Step).where(Step.run_id == run_id)
                )
            ).scalar_one()
        )


async def steps_for_node(run_id: uuid.UUID, node_id: str) -> list[uuid.UUID]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Step.id).where(Step.run_id == run_id, Step.node_id == node_id)
            )
        ).scalars().all()
        return list(rows)
