"""Declarative workflow graph loaded from YAML and validated on load."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

END_SENTINELS = {"end", "__end__", "$end", ""}

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = ROOT / "workflows"


class RetryPolicy(BaseModel):
    """Bounded retry configuration for a node."""

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_base_s: float = Field(default=0.01, ge=0.0)
    backoff_max_s: float = Field(default=2.0, ge=0.0)
    jitter: float = Field(default=0.1, ge=0.0, le=1.0)


class Node(BaseModel):
    """A single workflow node."""

    id: str
    capability: str
    agent: str | None = None
    description: str = ""
    next: list[str] = Field(default_factory=list)
    conditional: dict[str, str] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    on_failure: Literal["fail", "escalate"] = "escalate"
    compensate_on_failure: bool = True
    models: list[str] = Field(default_factory=list)
    loop_limit: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


class GraphValidationError(ValueError):
    """Raised when a workflow graph is structurally invalid."""


def is_end(target: str) -> bool:
    return target in END_SENTINELS


class WorkflowGraph(BaseModel):
    """A validated workflow graph."""

    name: str
    description: str = ""
    start: str
    max_iterations: int | None = None
    nodes: list[Node]

    _index: dict[str, Node] = {}

    @model_validator(mode="after")
    def _validate(self) -> WorkflowGraph:
        index: dict[str, Node] = {}
        for node in self.nodes:
            if node.id in index:
                raise GraphValidationError(f"duplicate node id '{node.id}'")
            if not node.capability:
                raise GraphValidationError(f"node '{node.id}' is missing a capability")
            index[node.id] = node
        if self.start not in index:
            raise GraphValidationError(f"start node '{self.start}' is not defined")
        for node in self.nodes:
            targets = list(node.next) + [t for t in node.conditional.values() if not is_end(t)]
            for target in targets:
                if target not in index:
                    raise GraphValidationError(
                        f"node '{node.id}' references unknown node '{target}'"
                    )
        cycles = self._find_cycles(index)
        if cycles and not self.max_iterations:
            bounded = all(
                any(index[n].loop_limit > 0 for n in cycle) for cycle in cycles
            )
            if not bounded:
                raise GraphValidationError(
                    "graph contains a cycle without a bound; set max_iterations or loop_limit"
                )
        object.__setattr__(self, "_index", index)
        return self

    def _find_cycles(self, index: dict[str, Node]) -> list[list[str]]:
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def targets(node: Node) -> list[str]:
            return [t for t in list(node.next) + list(node.conditional.values()) if not is_end(t)]

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            stack.append(node_id)
            on_stack.add(node_id)
            for target in targets(index[node_id]):
                if target in on_stack:
                    start = stack.index(target)
                    cycles.append(stack[start:])
                elif target not in visited:
                    dfs(target)
            stack.pop()
            on_stack.discard(node_id)

        for node_id in index:
            if node_id not in visited:
                dfs(node_id)
        return cycles

    @property
    def start_node(self) -> Node:
        return self._index[self.start]

    @classmethod
    def parse(cls, data: dict[str, Any]) -> WorkflowGraph:
        """Validate a mapping, surfacing :class:`GraphValidationError`."""

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise GraphValidationError(str(exc)) from exc

    def get(self, node_id: str) -> Node:
        try:
            return self._index[node_id]
        except KeyError:
            raise GraphValidationError(f"unknown node '{node_id}'") from None

    def has(self, node_id: str) -> bool:
        return node_id in self._index

    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def successors(self, node_id: str, verdict: str | None = None) -> list[str]:
        """Return the next node ids given an optional verifier verdict."""

        node = self.get(node_id)
        if node.conditional:
            if verdict is not None and verdict in node.conditional:
                target = node.conditional[verdict]
                return [] if is_end(target) else [target]
            if verdict is None:
                return [t for t in node.next if not is_end(t)]
        return [t for t in node.next if not is_end(t)]

    def to_dict(self) -> dict[str, Any]:
        """Serializable form for the API and the UI DAG renderer."""

        return {
            "name": self.name,
            "description": self.description,
            "start": self.start,
            "max_iterations": self.max_iterations,
            "nodes": [
                {
                    "id": n.id,
                    "capability": n.capability,
                    "agent": n.agent,
                    "description": n.description,
                    "next": n.next,
                    "conditional": n.conditional,
                    "retry": n.retry.model_dump(),
                    "on_failure": n.on_failure,
                    "models": n.models,
                    "loop_limit": n.loop_limit,
                }
                for n in self.nodes
            ],
        }

    def layers(self) -> dict[str, int]:
        """Assign each node a left-to-right layer for DAG layout.

        Back edges created by bounded revise loops are ignored so the layering
        is acyclic even when the graph is not a strict DAG.
        """

        index = self._index
        layer: dict[str, int] = {}
        on_stack: set[str] = set()

        def visit(node_id: str, depth: int) -> None:
            if node_id in on_stack:
                return
            if node_id in layer and layer[node_id] >= depth:
                return
            layer[node_id] = depth
            on_stack.add(node_id)
            node = index[node_id]
            targets = [
                t
                for t in list(node.next) + list(node.conditional.values())
                if not is_end(t) and t in index
            ]
            for target in targets:
                visit(target, depth + 1)
            on_stack.discard(node_id)

        visit(self.start, 0)
        for nid in self.node_ids():
            layer.setdefault(nid, 0)
        return layer


def load_graph(source: str | Path) -> WorkflowGraph:
    """Load a workflow graph from a YAML file or a workflow name/path."""

    path = Path(source)
    if not path.suffix:
        path = path.with_suffix(".yaml")
    if not path.exists():
        candidate = WORKFLOWS_DIR / path.name
        if candidate.exists():
            path = candidate
    if not path.exists():
        raise FileNotFoundError(f"workflow not found: {source}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return WorkflowGraph.parse(data)


def list_workflows() -> list[tuple[str, WorkflowGraph]]:
    """Load every workflow YAML in the workflows directory."""

    results: list[tuple[str, WorkflowGraph]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        try:
            results.append((path.stem, load_graph(path)))
        except GraphValidationError:
            continue
    return results
