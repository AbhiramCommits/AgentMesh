"""Process-wide accessor for the shared tool registry.

Both the in-process agent execution path and the MCP JSON-RPC endpoint use the
same registry instance so that capabilities and recordings are consistent.
"""

from __future__ import annotations

from agentmesh.tools import ToolRegistry, build_default_registry

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the process-wide tool registry, creating it on first use."""

    global _registry
    if _registry is None:
        _registry = build_default_registry()
    return _registry


def set_registry(registry: ToolRegistry) -> None:
    """Override the process-wide registry (tests)."""

    global _registry
    _registry = registry


def reset_registry() -> None:
    global _registry
    _registry = None
