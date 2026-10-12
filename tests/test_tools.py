"""Tests for the MCP-style tool registry and built-in tools."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from agentmesh.tools import (
    InMemoryToolRecorder,
    ToolArgError,
    ToolContext,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    ToolTimeout,
    build_default_registry,
)
from agentmesh.tools.mcp_server import handle_jsonrpc
from agentmesh.tools.sql_query import apply_limit, validate_select


class _EchoArgs(BaseModel):
    value: int


async def _echo(args: BaseModel, ctx: ToolContext) -> dict:
    return {"echo": args.value}  # type: ignore[attr-defined]


async def _slow(args: BaseModel, ctx: ToolContext) -> dict:
    await asyncio.sleep(5)
    return {"done": True}


def _registry_with(*specs: ToolSpec) -> tuple[ToolRegistry, InMemoryToolRecorder]:
    recorder = InMemoryToolRecorder()
    registry = ToolRegistry(recorder=recorder)
    for spec in specs:
        registry.register(spec)
    return registry, recorder


async def test_arg_validation_rejected_and_recorded() -> None:
    registry, recorder = _registry_with(
        ToolSpec(name="echo", description="echo", args_model=_EchoArgs, handler=_echo)
    )
    with pytest.raises(ToolArgError):
        await registry.invoke("echo", {"value": "not-an-int"})
    assert len(recorder.records) == 1
    assert recorder.records[0].ok is False
    assert "int" in (recorder.records[0].error or "")


async def test_timeout_raises_and_is_recorded() -> None:
    registry, recorder = _registry_with(
        ToolSpec(
            name="slow",
            description="slow",
            args_model=_EchoArgs,
            handler=_slow,
            timeout_s=0.05,
        )
    )
    with pytest.raises(ToolTimeout):
        await registry.invoke("slow", {"value": 1})
    assert recorder.records[0].timed_out is True
    assert recorder.records[0].ok is False


async def test_successful_call_records_latency() -> None:
    registry, recorder = _registry_with(
        ToolSpec(name="echo", description="echo", args_model=_EchoArgs, handler=_echo)
    )
    result = await registry.invoke("echo", {"value": 7})
    assert result == {"echo": 7}
    assert recorder.records[0].ok is True
    assert recorder.records[0].latency_ms >= 0


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM support_tickets",
        "UPDATE support_tickets SET status='x'",
        "SELECT 1; DROP TABLE support_tickets",
        "INSERT INTO support_tickets VALUES (1)",
        "DROP TABLE support_tickets",
        "WITH x AS (DELETE FROM support_tickets RETURNING *) SELECT * FROM x",
    ],
)
async def test_sql_non_select_rejected(query: str) -> None:
    registry, _ = _registry_with()
    recorder = InMemoryToolRecorder()
    registry = build_default_registry(recorder=recorder)
    with pytest.raises(ToolExecutionError):
        await registry.invoke("sql_query", {"query": query})
    assert recorder.records[-1].ok is False


def test_sql_limit_cap_enforced() -> None:
    assert apply_limit("SELECT * FROM support_tickets").endswith("LIMIT 100")
    assert apply_limit("SELECT * FROM support_tickets LIMIT 5").endswith("LIMIT 5")
    assert apply_limit("SELECT * FROM support_tickets LIMIT 99999").endswith("LIMIT 100")


def test_validate_select_allows_plain_select() -> None:
    assert validate_select("SELECT 1").lower().startswith("select")


async def test_sandbox_traversal_blocked(tmp_path) -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    ctx = ToolContext(sandbox_dir=tmp_path / "run1")
    with pytest.raises(ToolExecutionError):
        await registry.invoke(
            "file_sandbox", {"op": "write", "path": "../escape.txt", "content": "x"}, ctx=ctx
        )


async def test_sandbox_absolute_path_blocked(tmp_path) -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    ctx = ToolContext(sandbox_dir=tmp_path / "run1")
    with pytest.raises(ToolExecutionError):
        await registry.invoke(
            "file_sandbox", {"op": "read", "path": "/etc/passwd"}, ctx=ctx
        )


async def test_compensate_removes_file(tmp_path) -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    root = tmp_path / "run1"
    ctx = ToolContext(sandbox_dir=root)
    result = await registry.invoke(
        "file_sandbox", {"op": "write", "path": "notes/a.txt", "content": "hello"}, ctx=ctx
    )
    target = root.resolve() / "notes" / "a.txt"
    assert target.is_file()
    await registry.compensate("file_sandbox", {"path": "notes/a.txt"}, result, ctx)
    assert not target.exists()


async def test_tools_list_returns_valid_descriptors() -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    descriptors = registry.list_descriptors()
    names = {d["name"] for d in descriptors}
    assert names == {"sql_query", "http_fetch", "file_sandbox"}
    for descriptor in descriptors:
        assert descriptor["description"]
        assert descriptor["inputSchema"]["type"] == "object"
        assert "properties" in descriptor["inputSchema"]


async def test_mcp_tools_list_and_call() -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    listing = await handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, registry
    )
    assert len(listing["result"]["tools"]) == 3

    call = await handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "file_sandbox",
                "arguments": {"op": "write", "path": "x.txt", "content": "y"},
            },
        },
        registry,
    )
    assert call["result"]["isError"] is False

    bad = await handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "nope"}, registry
    )
    assert bad["error"]["code"] == -32601


@pytest.mark.integration
async def test_sql_query_success(database: None) -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    result = await registry.invoke(
        "sql_query",
        {"query": "SELECT ticket_ref, customer FROM support_tickets ORDER BY ticket_ref"},
    )
    assert result["row_count"] >= 5
    assert "ticket_ref" in result["columns"]


async def test_http_fetch_host_not_allowed() -> None:
    registry = build_default_registry(recorder=InMemoryToolRecorder())
    with pytest.raises(ToolExecutionError):
        await registry.invoke("http_fetch", {"url": "https://evil.example.net/"})


async def test_http_fetch_with_mock_transport() -> None:
    import httpx

    from agentmesh.tools.http_fetch import reset_allowed_hosts, set_http_transport

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world")

    set_http_transport(httpx.MockTransport(handler))
    try:
        registry = build_default_registry(recorder=InMemoryToolRecorder())
        result = await registry.invoke("http_fetch", {"url": "https://example.com/page"})
        assert result["status"] == 200
        assert result["text"] == "hello world"
    finally:
        set_http_transport(None)
        reset_allowed_hosts()
