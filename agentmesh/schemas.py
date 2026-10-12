"""Pydantic v2 schemas used by the API and internal boundaries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    """Request body for starting a run."""

    workflow_name: str = Field(default="demo", description="Workflow YAML name (no extension)")
    input: dict[str, Any] = Field(default_factory=dict)
    attempt_budget: int = Field(default=3, ge=0, le=10)
    crash_at_node: str | None = Field(
        default=None, description="Test hook: crash when this node starts"
    )


class ToolCallOut(BaseModel):
    """Serialized tool call."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    ok: bool
    error: str | None = None
    latency_ms: int
    timed_out: bool
    created_at: datetime


class RetrievalOut(BaseModel):
    """Serialized retrieval provenance record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    chunk_text: str | None = None
    score: float


class StepOut(BaseModel):
    """Serialized step, optionally with nested tool calls and retrievals."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_id: str
    agent_name: str | None = None
    status: str
    attempt: int
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    model_used: str | None = None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    retrievals: list[RetrievalOut] = Field(default_factory=list)


class EscalationOut(BaseModel):
    """Serialized escalation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_id: uuid.UUID | None = None
    reason: str
    status: str
    resolution: str | None = None
    created_at: datetime


class CostRollup(BaseModel):
    """Aggregated token/latency cost for a run."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    tool_calls: int = 0
    wall_clock_ms: int | None = None


class RunOut(BaseModel):
    """Serialized run summary."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_name: str
    status: str
    input: dict[str, Any]
    attempt_budget: int
    created_at: datetime
    updated_at: datetime
    cost: CostRollup | None = None


class RunDetail(RunOut):
    """Run plus its steps, escalations, workflow graph and cost rollup."""

    steps: list[StepOut] = Field(default_factory=list)
    escalations: list[EscalationOut] = Field(default_factory=list)
    graph: dict[str, Any] | None = None


class EscalationResolve(BaseModel):
    """Request body for resolving an escalation."""

    resolution: str


class ToolDescriptor(BaseModel):
    """MCP-style tool descriptor."""

    name: str
    description: str
    inputSchema: dict[str, Any]


class WorkflowSummary(BaseModel):
    """Summary of a discoverable workflow."""

    name: str
    description: str
    nodes: int
