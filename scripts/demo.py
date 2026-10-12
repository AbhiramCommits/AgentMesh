"""Run a workflow end to end and print a timeline.

Usage::

    python scripts/demo.py                       # demo workflow
    python scripts/demo.py field_ops_revise      # 6-node revise variant
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentmesh.db import session_scope  # noqa: E402
from agentmesh.engine.executor import Runtime, execute_run  # noqa: E402
from agentmesh.models import Run, RunStatus, Step  # noqa: E402

DEFAULT_REQUEST = "Which ticket categories drive the most open support load?"


async def run_demo(workflow: str, request: str) -> None:
    async with session_scope() as session:
        run = Run(
            workflow_name=workflow,
            status=RunStatus.PENDING.value,
            input={"request": request},
            attempt_budget=3,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    print(f"Starting workflow '{workflow}' for request: {request!r}")
    state = await execute_run(run_id, runtime=Runtime.build())

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        steps = (
            await session.execute(
                select(Step).where(Step.run_id == run_id).order_by(Step.created_at)
            )
        ).scalars().all()

    print(f"\nRun {run_id} status: {run.status if run else 'missing'}")
    print("-" * 72)
    for step in steps:
        print(
            f"{step.node_id:<10} {step.status:<10} agent={step.agent_name or '-':<12} "
            f"model={step.model_used or '-':<14} "
            f"{step.prompt_tokens + step.completion_tokens:>5} tok "
            f"{step.latency_ms:>5} ms"
        )
    print("-" * 72)
    print(f"steps executed : {len(steps)}")
    print(f"distinct nodes : {len({s.node_id for s in steps})}")
    print(f"final state    : {state.get('current_node') or 'end'}")
    print(f"verdicts       : {state.get('verdicts')}")


def main() -> None:
    workflow = sys.argv[1] if len(sys.argv) > 1 else "demo"
    asyncio.run(run_demo(workflow, DEFAULT_REQUEST))


if __name__ == "__main__":
    main()
