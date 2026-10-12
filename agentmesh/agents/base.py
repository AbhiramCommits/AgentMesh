"""Agent protocol, run context, and result types."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agentmesh.llm.provider import ModelProvider
from agentmesh.tools.base import ToolRegistry

if TYPE_CHECKING:
    from agentmesh.engine.graph import Node

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response, if any."""

    match = _JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class StepResult:
    """Outcome of a single agent execution."""

    output: dict[str, Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_used: str = ""
    latency_ms: int = 0
    verdict: str | None = None
    error: str | None = None


@dataclass
class RunContext:
    """Everything an agent needs to execute one node."""

    run_id: uuid.UUID
    node: Node
    step_id: uuid.UUID
    run_input: dict[str, Any]
    provider: ModelProvider
    registry: ToolRegistry
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    iterations: dict[str, int] = field(default_factory=dict)
    attempt: int = 1
    model: str = ""
    models: list[str] = field(default_factory=list)
    sandbox_dir: Path | None = None

    @property
    def node_id(self) -> str:
        return self.node.id

    @property
    def capability(self) -> str:
        return self.node.capability

    def evidence(self) -> list[dict[str, Any]]:
        """Collect retrieved evidence from upstream retrieve nodes."""

        evidence: list[dict[str, Any]] = []
        for output in self.outputs.values():
            if isinstance(output, dict) and output.get("evidence"):
                evidence.extend(output["evidence"])
        return evidence

    def latest_answer(self) -> str:
        """Return the most recent draft answer produced upstream."""

        for output in reversed(list(self.outputs.values())):
            if isinstance(output, dict) and output.get("answer"):
                return str(output["answer"])
        return ""


class Agent(Protocol):
    """A capability-owning worker."""

    name: str
    capabilities: tuple[str, ...]

    async def run(self, ctx: RunContext) -> StepResult:  # pragma: no cover
        ...
