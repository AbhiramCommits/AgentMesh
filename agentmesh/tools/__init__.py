"""Tool registry and the three built-in AgentMesh tools."""

from __future__ import annotations

from agentmesh.tools.base import (
    DatabaseToolRecorder,
    InMemoryToolRecorder,
    ToolArgError,
    ToolCallRecord,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolNotFound,
    ToolRegistry,
    ToolSpec,
    ToolTimeout,
)
from agentmesh.tools.file_sandbox import FILE_SANDBOX_SPEC
from agentmesh.tools.http_fetch import HTTP_FETCH_SPEC
from agentmesh.tools.sql_query import SQL_QUERY_SPEC


def build_default_registry(recorder=None) -> ToolRegistry:
    """Create a registry with the three built-in tools registered.

    The default recorder persists to the ``tool_calls`` table; pass an
    :class:`~agentmesh.tools.base.InMemoryToolRecorder` for offline use.
    """

    registry = ToolRegistry(recorder=recorder or DatabaseToolRecorder())
    registry.register(SQL_QUERY_SPEC)
    registry.register(HTTP_FETCH_SPEC)
    registry.register(FILE_SANDBOX_SPEC)
    return registry


__all__ = [
    "DatabaseToolRecorder",
    "FILE_SANDBOX_SPEC",
    "HTTP_FETCH_SPEC",
    "InMemoryToolRecorder",
    "SQL_QUERY_SPEC",
    "ToolArgError",
    "ToolCallRecord",
    "ToolContext",
    "ToolError",
    "ToolExecutionError",
    "ToolNotFound",
    "ToolRegistry",
    "ToolSpec",
    "ToolTimeout",
    "build_default_registry",
]
