import { describe, expect, it } from "vitest";
import { computeLayers, layoutDag } from "./dag";
import type { WorkflowGraph } from "../api/types";

const graph: WorkflowGraph = {
  name: "demo",
  description: "demo",
  start: "plan",
  max_iterations: 50,
  nodes: [
    {
      id: "plan",
      capability: "plan",
      agent: "planner",
      description: "",
      next: ["retrieve"],
      conditional: {},
      models: [],
      loop_limit: 0,
    },
    {
      id: "retrieve",
      capability: "retrieve",
      agent: "retriever",
      description: "",
      next: ["analyze"],
      conditional: {},
      models: [],
      loop_limit: 0,
    },
    {
      id: "analyze",
      capability: "analyze",
      agent: "analyst",
      description: "",
      next: ["verify"],
      conditional: {},
      models: [],
      loop_limit: 0,
    },
    {
      id: "verify",
      capability: "verify",
      agent: "verifier",
      description: "",
      next: [],
      conditional: { pass: "end", revise: "analyze", fail: "end" },
      models: [],
      loop_limit: 0,
    },
  ],
};

describe("computeLayers", () => {
  it("assigns left-to-right layers ignoring the revise back edge", () => {
    const layers = computeLayers(graph);
    expect(layers.plan).toBe(0);
    expect(layers.retrieve).toBe(1);
    expect(layers.analyze).toBe(2);
    expect(layers.verify).toBe(3);
  });
});

describe("layoutDag", () => {
  it("places every node once and skips end sentinels", () => {
    const layout = layoutDag(graph, { analyze: "completed" });
    expect(layout.nodes).toHaveLength(4);
    expect(layout.edges).toContainEqual({ from: "plan", to: "retrieve" });
    expect(layout.edges).toContainEqual({ from: "verify", to: "analyze", label: "revise" });
    // no edge targets the "end" sentinel
    expect(layout.edges.every((e) => e.to !== "end")).toBe(true);
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });

  it("applies node statuses", () => {
    const layout = layoutDag(graph, { plan: "completed", retrieve: "running" });
    const plan = layout.nodes.find((n) => n.id === "plan");
    const retrieve = layout.nodes.find((n) => n.id === "retrieve");
    expect(plan?.status).toBe("completed");
    expect(retrieve?.status).toBe("running");
  });
});
