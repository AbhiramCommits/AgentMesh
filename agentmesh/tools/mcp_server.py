"""Minimal MCP-style JSON-RPC endpoint exposing the tool registry.

Implements the two MCP methods an external client needs to discover and call
tools: ``tools/list`` and ``tools/call``. The in-process agent path uses the
same :class:`~agentmesh.tools.base.ToolRegistry`, so behaviour is identical.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agentmesh.tools.base import ToolContext, ToolError, ToolRegistry
from agentmesh.tools.runtime import get_registry

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def handle_jsonrpc(payload: dict[str, Any], registry: ToolRegistry) -> dict[str, Any]:
    """Dispatch a single JSON-RPC request against the registry."""

    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return _error(request_id, INVALID_REQUEST, "invalid JSON-RPC request")

    if method in {"tools/list", "list_tools"}:
        return _ok(request_id, {"tools": registry.list_descriptors()})

    if method in {"tools/call", "call_tool"}:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _error(request_id, INVALID_PARAMS, "params.name is required")
        ctx = ToolContext(extras={"source": "mcp"})
        try:
            result = await registry.invoke(name, arguments, ctx=ctx)
            is_error = False
            text = json.dumps(result, default=str)
        except ToolError as exc:
            is_error = True
            text = str(exc)
        return _ok(
            request_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )

    return _error(request_id, METHOD_NOT_FOUND, f"unknown method '{method}'")


def build_mcp_router(registry: ToolRegistry | None = None) -> APIRouter:
    """Build a FastAPI router serving JSON-RPC at ``POST /mcp``."""

    router = APIRouter(tags=["mcp"])
    active = registry or get_registry()

    @router.post("/mcp")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(_error(None, PARSE_ERROR, "invalid JSON"))
        if isinstance(payload, list):
            responses = [await handle_jsonrpc(item, active) for item in payload]
            return JSONResponse(responses)
        response = await handle_jsonrpc(payload, active)
        return JSONResponse(response)

    return router
