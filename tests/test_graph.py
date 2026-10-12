"""Tests for workflow graph loading and validation."""

from __future__ import annotations

import pytest

from agentmesh.engine.graph import (
    GraphValidationError,
    WorkflowGraph,
    load_graph,
)


def _node(node_id: str, capability: str, **extra) -> dict:
    base = {"id": node_id, "capability": capability}
    base.update(extra)
    return base


def test_load_demo_graph() -> None:
    graph = load_graph("demo")
    assert graph.name == "demo"
    assert graph.start == "plan"
    assert graph.node_ids() == ["plan", "retrieve", "analyze", "verify"]
    assert graph.successors("plan") == ["retrieve"]
    assert graph.successors("verify", "pass") == []
    assert graph.successors("verify", "revise") == ["analyze"]


def test_layers_left_to_right() -> None:
    graph = load_graph("demo")
    layers = graph.layers()
    assert layers["plan"] < layers["retrieve"] < layers["analyze"] < layers["verify"]


def test_unknown_node_reference_rejected() -> None:
    with pytest.raises(GraphValidationError):
        WorkflowGraph.parse(
            {
                "name": "bad",
                "start": "a",
                "nodes": [_node("a", "plan", next=["ghost"])],
            }
        )


def test_missing_capability_rejected() -> None:
    with pytest.raises(GraphValidationError):
        WorkflowGraph.parse(
            {"name": "bad", "start": "a", "nodes": [{"id": "a", "capability": ""}]}
        )


def test_cycle_without_bound_rejected() -> None:
    with pytest.raises(GraphValidationError):
        WorkflowGraph.parse(
            {
                "name": "loopy",
                "start": "a",
                "nodes": [
                    _node("a", "analyze", next=["b"]),
                    _node("b", "verify", next=["a"]),
                ],
            }
        )


def test_cycle_with_max_iterations_allowed() -> None:
    graph = WorkflowGraph.parse(
        {
            "name": "loopy-bounded",
            "start": "a",
            "max_iterations": 5,
            "nodes": [
                _node("a", "analyze", next=["b"]),
                _node("b", "verify", next=["a"]),
            ],
        }
    )
    assert graph.max_iterations == 5


def test_duplicate_node_id_rejected() -> None:
    with pytest.raises(GraphValidationError):
        WorkflowGraph.parse(
            {
                "name": "dup",
                "start": "a",
                "nodes": [_node("a", "plan"), _node("a", "verify")],
            }
        )
