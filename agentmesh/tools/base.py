"""MCP-style typed tool registry.

Tools are described by :class:`ToolSpec`, which pairs a Pydantic v2 argument
model with a handler coroutine. The registry validates arguments, enforces a
per-tool timeout, measures latency, and records every attempt (success, error,
or timeout) through a pluggable recorder.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import insert


class ToolError(Exception):
    """Base class for tool-layer failures."""


class ToolArgError(ToolError):
    """Raised when raw arguments fail validation against the tool's model."""

    def __init__(self, tool_name: str, detail: Any) -> None:
        self.tool_name = tool_name
        self.detail = detail
        super().__init__(f"invalid args for tool '{tool_name}': {detail}")


class ToolTimeout(ToolError):
    """Raised when a tool invocation exceeds its configured timeout."""

    def __init__(self, tool_name: str, timeout_s: float) -> None:
        self.tool_name = tool_name
        self.timeout_s = timeout_s
        super().__init__(f"tool '{tool_name}' timed out after {timeout_s}s")


class ToolNotFound(ToolError):
    """Raised when an unknown tool is requested."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"unknown tool '{tool_name}'")


class ToolExecutionError(ToolError):
    """Raised when a tool handler fails for any non-timeout reason."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"tool '{tool_name}' failed: {message}")


@dataclass
class ToolContext:
    """Per-invocation context handed to tool handlers."""

    run_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    sandbox_dir: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[Any]]
CompensateFn = Callable[[BaseModel | dict[str, Any], Any, ToolContext], Awaitable[None]]


@dataclass
class ToolSpec:
    """Declarative description of a tool."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    timeout_s: float = 30.0
    idempotent: bool = True
    compensate: CompensateFn | None = None
    tags: tuple[str, ...] = ()

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON schema for the tool arguments."""

        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def descriptor(self) -> dict[str, Any]:
        """Return an MCP ``tools/list``-style descriptor."""

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }


@dataclass
class ToolCallRecord:
    """A persisted record of a single tool attempt."""

    tool_name: str
    args: dict[str, Any] | None
    result: Any
    ok: bool
    error: str | None
    latency_ms: int
    timed_out: bool
    step_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


class ToolCallRecorder(Protocol):
    """Protocol for persisting :class:`ToolCallRecord` instances."""

    async def __call__(self, record: ToolCallRecord) -> None:  # pragma: no cover
        ...


class InMemoryToolRecorder:
    """Recorder that keeps records in a list (tests and benchmarks)."""

    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    async def __call__(self, record: ToolCallRecord) -> None:
        self.records.append(record)


class DatabaseToolRecorder:
    """Recorder that writes ``tool_calls`` rows via a session factory."""

    async def __call__(self, record: ToolCallRecord) -> None:
        if record.step_id is None:
            return
        from agentmesh.db import session_scope
        from agentmesh.models import ToolCall

        async with session_scope() as session:
            result = record.result
            if result is not None and not isinstance(result, dict):
                result = {"value": result}
            await session.execute(
                insert(ToolCall).values(
                    step_id=record.step_id,
                    tool_name=record.tool_name,
                    args=record.args,
                    result=result,
                    ok=record.ok,
                    error=record.error,
                    latency_ms=record.latency_ms,
                    timed_out=record.timed_out,
                )
            )


class ToolRegistry:
    """Registry of :class:`ToolSpec` objects with validation and timeouts."""

    def __init__(self, recorder: ToolCallRecorder | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._recorder: ToolCallRecorder = recorder or InMemoryToolRecorder()

    def register(self, spec: ToolSpec) -> None:
        """Register a tool, replacing any prior definition of the same name."""

        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        """Return a tool spec, raising :class:`ToolNotFound` if absent."""

        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(name) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list_descriptors(self) -> list[dict[str, Any]]:
        """Return MCP-style descriptors for all registered tools."""

        return [spec.descriptor() for spec in self._tools.values()]

    def set_recorder(self, recorder: ToolCallRecorder) -> None:
        self._recorder = recorder

    async def invoke(
        self,
        name: str,
        raw_args: dict[str, Any] | None = None,
        *,
        step_id: uuid.UUID | None = None,
        ctx: ToolContext | None = None,
    ) -> Any:
        """Validate, execute, time out, measure, and record a tool call."""

        raw_args = raw_args or {}
        spec = self.get(name)
        context = ctx or ToolContext(step_id=step_id)

        try:
            args = spec.args_model.model_validate(raw_args)
        except ValidationError as exc:
            record = ToolCallRecord(
                tool_name=name,
                args=raw_args,
                result=None,
                ok=False,
                error=str(exc),
                latency_ms=0,
                timed_out=False,
                step_id=step_id,
                run_id=context.run_id,
            )
            await self._recorder(record)
            raise ToolArgError(name, exc.errors()) from exc

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(spec.handler(args, context), spec.timeout_s)
        except TimeoutError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._recorder(
                ToolCallRecord(
                    tool_name=name,
                    args=raw_args,
                    result=None,
                    ok=False,
                    error=f"timeout after {spec.timeout_s}s",
                    latency_ms=latency_ms,
                    timed_out=True,
                    step_id=step_id,
                    run_id=context.run_id,
                )
            )
            raise ToolTimeout(name, spec.timeout_s) from exc
        except ToolError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._recorder(
                ToolCallRecord(
                    tool_name=name,
                    args=raw_args,
                    result=None,
                    ok=False,
                    error=str(exc),
                    latency_ms=latency_ms,
                    timed_out=False,
                    step_id=step_id,
                    run_id=context.run_id,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._recorder(
                ToolCallRecord(
                    tool_name=name,
                    args=raw_args,
                    result=None,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=latency_ms,
                    timed_out=False,
                    step_id=step_id,
                    run_id=context.run_id,
                )
            )
            raise ToolExecutionError(name, f"{type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        await self._recorder(
            ToolCallRecord(
                tool_name=name,
                args=raw_args,
                result=result,
                ok=True,
                error=None,
                latency_ms=latency_ms,
                timed_out=False,
                step_id=step_id,
                run_id=context.run_id,
            )
        )
        return result

    async def compensate(self, name: str, args: dict[str, Any], result: Any, ctx: ToolContext) -> None:
        """Run a tool's compensating action, if it declares one."""

        spec = self.get(name)
        if spec.compensate is None:
            return
        await spec.compensate(args, result, ctx)
