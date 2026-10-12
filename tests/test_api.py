"""Integration tests for the FastAPI orchestration API."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agentmesh.api.main import app


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _wait_for(client: httpx.AsyncClient, run_id: str, *statuses: str, timeout: float = 40.0):
    loops = int(timeout / 0.1)
    last = None
    for _ in range(loops):
        response = await client.get(f"/runs/{run_id}")
        last = response.json()
        if last["status"] in statuses:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach {statuses}; last={last}")


@pytest.mark.integration
async def test_health_workflows_and_tools(database: None) -> None:
    async with _client() as client:
        health = (await client.get("/health")).json()
        assert health["status"] == "ok"

        workflows = (await client.get("/workflows")).json()
        assert any(w["name"] == "demo" for w in workflows)

        tools = (await client.get("/tools")).json()
        names = {t["name"] for t in tools["tools"]}
        assert {"sql_query", "http_fetch", "file_sandbox"} <= names


@pytest.mark.integration
async def test_start_run_completes_with_cost(database: None) -> None:
    async with _client() as client:
        created = (
            await client.post(
                "/runs",
                json={
                    "workflow_name": "demo",
                    "input": {"request": "Which categories drive open tickets?"},
                },
            )
        ).json()
        run_id = created["id"]
        detail = await _wait_for(client, run_id, "completed", "failed")
        assert detail["status"] == "completed"
        assert [s["node_id"] for s in detail["steps"]] == ["plan", "retrieve", "analyze", "verify"]
        assert detail["cost"]["total_tokens"] > 0
        assert detail["cost"]["tool_calls"] >= 1
        assert detail["graph"]["name"] == "demo"


@pytest.mark.integration
async def test_crash_then_resume_via_api(database: None) -> None:
    async with _client() as client:
        created = (
            await client.post(
                "/runs",
                json={
                    "workflow_name": "demo",
                    "input": {"request": "Summarize open ticket load"},
                    "crash_at_node": "analyze",
                },
            )
        ).json()
        run_id = created["id"]
        crashed = await _wait_for(client, run_id, "failed")
        assert crashed["status"] == "failed"

        await client.post(f"/runs/{run_id}/resume")
        resumed = await _wait_for(client, run_id, "completed", "failed")
        assert resumed["status"] == "completed"
        analyze_steps = [s for s in resumed["steps"] if s["node_id"] == "analyze"]
        assert len(analyze_steps) == 2  # crashed attempt + resumed attempt


@pytest.mark.integration
async def test_replay_from_node(database: None) -> None:
    async with _client() as client:
        created = (
            await client.post(
                "/runs",
                json={"workflow_name": "demo", "input": {"request": "Ticket load"}},
            )
        ).json()
        original = await _wait_for(client, created["id"], "completed", "failed")

        replay = (
            await client.post(f"/runs/{created['id']}/replay", params={"from_node": "verify"})
        ).json()
        assert replay["input"]["replay_from"] == "verify"
        detail = await _wait_for(client, replay["id"], "completed", "failed")
        assert [s["node_id"] for s in detail["steps"]] == ["verify"]
        assert original["status"] == "completed"


@pytest.mark.integration
async def test_events_stream_emits_transitions(database: None) -> None:
    async with _client() as client:
        created = (
            await client.post(
                "/runs", json={"workflow_name": "demo", "input": {"request": "Events"}}
            )
        ).json()
        run_id = created["id"]
        events: list[str] = []
        async with client.stream("GET", f"/runs/{run_id}/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "event: end" in line:
                    break
                if len(events) > 100:
                    break
        assert "step" in events
        assert "run" in events
        assert "end" in events


@pytest.mark.integration
async def test_escalations_endpoint(database: None) -> None:
    async with _client() as client:
        response = await client.get("/escalations", params={"status": "open"})
        assert response.status_code == 200
        assert isinstance(response.json(), list)
