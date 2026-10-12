// DAG layout for the workflow graph (SVG, layered left-to-right).
import type { Step, StepStatus, WorkflowGraph } from "../api/types";

const END = new Set(["end", "__end__", "$end", ""]);

export interface DagNode {
  id: string;
  layer: number;
  index: number;
  x: number;
  y: number;
  capability: string;
  status: StepStatus;
}

export interface DagEdge {
  from: string;
  to: string;
  label?: string;
}

export interface DagLayout {
  nodes: DagNode[];
  edges: DagEdge[];
  width: number;
  height: number;
}

export function computeLayers(graph: WorkflowGraph): Record<string, number> {
  const index = new Map(graph.nodes.map((n) => [n.id, n]));
  const layer: Record<string, number> = {};
  const onStack = new Set<string>();

  const visit = (id: string, depth: number): void => {
    if (onStack.has(id)) return;
    if (layer[id] !== undefined && layer[id] >= depth) return;
    layer[id] = depth;
    onStack.add(id);
    const node = index.get(id);
    if (node) {
      const targets = [...node.next, ...Object.values(node.conditional)].filter(
        (t) => !END.has(t) && index.has(t),
      );
      for (const target of targets) visit(target, depth + 1);
    }
    onStack.delete(id);
  };

  visit(graph.start, 0);
  for (const node of graph.nodes) if (layer[node.id] === undefined) layer[node.id] = 0;
  return layer;
}

/** Map each node id to the status of its most recent step execution. */
export function statusByNode(steps: Step[]): Record<string, StepStatus> {
  const result: Record<string, StepStatus> = {};
  for (const step of steps) result[step.node_id] = step.status;
  return result;
}

export function layoutDag(
  graph: WorkflowGraph,
  statuses: Record<string, StepStatus> = {},
): DagLayout {
  const layers = computeLayers(graph);
  const byLayer: Record<number, string[]> = {};
  for (const node of graph.nodes) {
    const layer = layers[node.id] ?? 0;
    (byLayer[layer] ??= []).push(node.id);
  }

  const X = 200;
  const Y = 110;
  const OX = 60;
  const OY = 50;
  const capabilityOf = new Map(graph.nodes.map((n) => [n.id, n.capability]));

  const nodes: DagNode[] = [];
  const layerKeys = Object.keys(byLayer)
    .map(Number)
    .sort((a, b) => a - b);
  for (const layer of layerKeys) {
    byLayer[layer].forEach((id, index) => {
      nodes.push({
        id,
        layer,
        index,
        x: OX + layer * X,
        y: OY + index * Y,
        capability: capabilityOf.get(id) ?? "",
        status: statuses[id] ?? "pending",
      });
    });
  }

  const position = new Map(nodes.map((n) => [n.id, n]));
  const edges: DagEdge[] = [];
  for (const node of graph.nodes) {
    for (const target of node.next) {
      if (!END.has(target) && position.has(target)) {
        edges.push({ from: node.id, to: target });
      }
    }
    for (const [label, target] of Object.entries(node.conditional)) {
      if (!END.has(target) && position.has(target)) {
        edges.push({ from: node.id, to: target, label });
      }
    }
  }

  const maxLayer = layerKeys.length ? layerKeys[layerKeys.length - 1] : 0;
  const maxRows = Math.max(1, ...Object.values(byLayer).map((a) => a.length));
  return {
    nodes,
    edges,
    width: OX + (maxLayer + 1) * X,
    height: OY + maxRows * Y,
  };
}
