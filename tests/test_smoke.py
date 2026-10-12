"""Step 1 smoke test: open a session and round-trip a run row."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agentmesh.db import session_scope
from agentmesh.models import Run, RunStatus


@pytest.mark.integration
async def test_insert_and_read_run(database: None) -> None:
    async with session_scope() as session:
        run = Run(
            workflow_name="demo",
            status=RunStatus.PENDING.value,
            input={"request": "smoke"},
            attempt_budget=3,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    async with session_scope() as session:
        fetched = (
            await session.execute(select(Run).where(Run.id == run_id))
        ).scalar_one()
        assert fetched.workflow_name == "demo"
        assert fetched.input["request"] == "smoke"
        assert fetched.status == RunStatus.PENDING.value
        assert fetched.attempt_budget == 3
