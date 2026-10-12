"""FastAPI orchestration API with SSE, replay/resume, and MCP mounting."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentmesh import __version__
from agentmesh.config import get_settings
from agentmesh.db import get_session
from agentmesh.engine.executor import Runtime, execute_run, resume_run
from agentmesh.engine.graph import list_workflows, load_graph
from agentmesh.engine.policies import resolve_escalation
from agentmesh.models import Escalation, Run, RunStatus, Step
from agentmesh.schemas import (
    CostRollup,
    EscalationOut,
    EscalationResolve,
    RetrievalOut,
    RunCreate,
    RunDetail,
    RunOut,
    StepOut,
    ToolCallOut,
    WorkflowSummary,
)
from agentmesh.tools.mcp_server import build_mcp_router
from agentmesh.tools.runtime import get_registry

TERMINAL = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.AWAITING_HUMAN.value}

_TASKS: set[asyncio.Task] = set()
_RUNTIME = Runtime.build()


def schedule(coro: Any) -> None:
    """Schedule a background coroutine, keeping a reference to prevent GC."""

    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _cost_from_steps(steps: list[Step]) -> CostRollup:
    prompt = sum(s.prompt_tokens for s in steps)
    completion = sum(s.completion_tokens for s in steps)
    latency = sum(s.latency_ms for s in steps)
    tool_calls = sum(len(s.tool_calls) for s in steps)
    started = [s.started_at for s in steps if s.started_at]
    finished = [s.finished_at for s in steps if s.finished_at]
    wall_clock = None
    if started and finished:
        wall_clock = int((max(finished) - min(started)).total_seconds() * 1000)
    return CostRollup(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        latency_ms=latency,
        tool_calls=tool_calls,
        wall_clock_ms=wall_clock,
    )


def _step_out(step: Step) -> StepOut:
    return StepOut(
        id=step.id,
        node_id=step.node_id,
        agent_name=step.agent_name,
        status=step.status,
        attempt=step.attempt,
        input=step.input,
        output=step.output,
        error=step.error,
        model_used=step.model_used,
        prompt_tokens=step.prompt_tokens,
        completion_tokens=step.completion_tokens,
        latency_ms=step.latency_ms,
        started_at=step.started_at,
        finished_at=step.finished_at,
        created_at=step.created_at,
        tool_calls=[ToolCallOut.model_validate(tc) for tc in step.tool_calls],
        retrievals=[RetrievalOut.model_validate(r) for r in step.retrievals],
    )


async def _load_run_detail(session: AsyncSession, run_id: uuid.UUID) -> RunDetail:
    run = (
        await session.execute(
            select(Run)
            .where(Run.id == run_id)
            .options(
                selectinload(Run.steps).selectinload(Step.tool_calls),
                selectinload(Run.steps).selectinload(Step.retrievals),
                selectinload(Run.escalations),
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    steps = [_step_out(s) for s in run.steps]
    cost = _cost_from_steps(list(run.steps))
    graph = None
    try:
        graph = load_graph(run.workflow_name).to_dict()
    except Exception:  # noqa: BLE001
        graph = None
    return RunDetail(
        id=run.id,
        workflow_name=run.workflow_name,
        status=run.status,
        input=run.input,
        attempt_budget=run.attempt_budget,
        created_at=run.created_at,
        updated_at=run.updated_at,
        cost=cost,
        steps=steps,
        escalations=[EscalationOut.model_validate(e) for e in run.escalations],
        graph=graph,
    )


def create_app() -> FastAPI:
    """Build the FastAPI application."""

    settings = get_settings()
    app = FastAPI(title="AgentMesh", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "provider": settings.provider}

    @router.get("/workflows", response_model=list[WorkflowSummary])
    async def workflows() -> list[WorkflowSummary]:
        return [
            WorkflowSummary(name=name, description=g.description, nodes=len(g.nodes))
            for name, g in list_workflows()
        ]

    @router.get("/tools")
    async def tools() -> dict[str, Any]:
        return {"tools": get_registry().list_descriptors()}

    @router.post("/runs", response_model=RunOut)
    async def start_run(
        payload: RunCreate, session: AsyncSession = Depends(get_session)
    ) -> RunOut:
        try:
            load_graph(payload.workflow_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run = Run(
            workflow_name=payload.workflow_name,
            status=RunStatus.PENDING.value,
            input=payload.input,
            attempt_budget=payload.attempt_budget,
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        run_id = run.id
        await session.commit()
        schedule(
            execute_run(run_id, crash_at_node=payload.crash_at_node, runtime=_RUNTIME)
        )
        return RunOut(
            id=run.id,
            workflow_name=run.workflow_name,
            status=run.status,
            input=run.input,
            attempt_budget=run.attempt_budget,
            created_at=run.created_at,
            updated_at=run.updated_at,
            cost=CostRollup(),
        )

    @router.get("/runs", response_model=list[RunOut])
    async def list_runs(
        limit: int = Query(default=50, le=200), session: AsyncSession = Depends(get_session)
    ) -> list[RunOut]:
        runs = (
            await session.execute(select(Run).order_by(Run.created_at.desc()).limit(limit))
        ).scalars().all()
        return [
            RunOut(
                id=r.id,
                workflow_name=r.workflow_name,
                status=r.status,
                input=r.input,
                attempt_budget=r.attempt_budget,
                created_at=r.created_at,
                updated_at=r.updated_at,
                cost=None,
            )
            for r in runs
        ]

    @router.get("/runs/{run_id}", response_model=RunDetail)
    async def get_run(
        run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
    ) -> RunDetail:
        return await _load_run_detail(session, run_id)

    @router.post("/runs/{run_id}/resume", response_model=RunDetail)
    async def resume(
        run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
    ) -> RunDetail:
        run = await session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        await resume_run(run_id, runtime=_RUNTIME)
        session.expire_all()
        return await _load_run_detail(session, run_id)

    @router.post("/runs/{run_id}/replay", response_model=RunOut)
    async def replay(
        run_id: uuid.UUID,
        from_node: str = Query(..., description="Node id to replay from"),
        session: AsyncSession = Depends(get_session),
    ) -> RunOut:
        original = await session.get(Run, run_id)
        if original is None:
            raise HTTPException(status_code=404, detail="run not found")
        graph = load_graph(original.workflow_name)
        if not graph.has(from_node):
            raise HTTPException(status_code=400, detail=f"unknown node '{from_node}'")
        new_run = Run(
            workflow_name=original.workflow_name,
            status=RunStatus.PENDING.value,
            input={**original.input, "replay_from": from_node, "replay_of": str(run_id)},
            attempt_budget=original.attempt_budget,
        )
        session.add(new_run)
        await session.flush()
        await session.refresh(new_run)
        new_id = new_run.id
        await session.commit()
        schedule(execute_run(new_id, runtime=_RUNTIME, start_node=from_node))
        return RunOut(
            id=new_run.id,
            workflow_name=new_run.workflow_name,
            status=new_run.status,
            input=new_run.input,
            attempt_budget=new_run.attempt_budget,
            created_at=new_run.created_at,
            updated_at=new_run.updated_at,
            cost=CostRollup(),
        )

    @router.get("/runs/{run_id}/events")
    async def events(run_id: uuid.UUID) -> StreamingResponse:
        async def stream() -> Any:
            seen: dict[uuid.UUID, str] = {}
            last_status: str | None = None
            for _ in range(3600):
                detail = await _get_detail(run_id)
                for step in detail.steps:
                    if seen.get(step.id) != step.status:
                        seen[step.id] = step.status
                        yield _sse("step", step.model_dump(mode="json"))
                if detail.status != last_status:
                    last_status = detail.status
                    yield _sse("run", {"id": str(run_id), "status": detail.status})
                running = any(s.status == "running" for s in detail.steps)
                if detail.status in TERMINAL and not running:
                    yield _sse("end", {"id": str(run_id), "status": detail.status})
                    break
                await asyncio.sleep(0.4)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get("/escalations", response_model=list[EscalationOut])
    async def list_escalations(
        status: str | None = None, session: AsyncSession = Depends(get_session)
    ) -> list[EscalationOut]:
        stmt = select(Escalation).order_by(Escalation.created_at.desc())
        if status:
            stmt = stmt.where(Escalation.status == status)
        rows = (await session.execute(stmt)).scalars().all()
        return [EscalationOut.model_validate(e) for e in rows]

    @router.post("/escalations/{escalation_id}/resolve", response_model=EscalationOut)
    async def resolve(
        escalation_id: uuid.UUID,
        payload: EscalationResolve,
        session: AsyncSession = Depends(get_session),
    ) -> EscalationOut:
        escalation = await session.get(Escalation, escalation_id)
        if escalation is None:
            raise HTTPException(status_code=404, detail="escalation not found")
        run_id = await resolve_escalation(escalation_id, payload.resolution, runtime=_RUNTIME)
        session.expire_all()
        escalation = await session.get(Escalation, escalation_id)
        _ = run_id
        assert escalation is not None
        return EscalationOut.model_validate(escalation)

    app.include_router(router)
    app.include_router(build_mcp_router())
    return app


async def _get_detail(run_id: uuid.UUID) -> RunDetail:
    from agentmesh.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        return await _load_run_detail(session, run_id)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


app = create_app()
