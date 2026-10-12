import type { RunDetail, StepStatus } from "../api/types";
import { layoutDag, statusByNode } from "../lib/dag";

interface Props {
  run: RunDetail;
  onSelect: (nodeId: string) => void;
}

const COLORS: Record<StepStatus, { fill: string; stroke: string }> = {
  pending: { fill: "#20242e", stroke: "#3a4150" },
  running: { fill: "#1d3557", stroke: "#4c8bf5" },
  completed: { fill: "#17402a", stroke: "#38a169" },
  failed: { fill: "#4a1f1f", stroke: "#e53e3e" },
  skipped: { fill: "#2a2f3a", stroke: "#4a4f5a" },
};

export function Dag({ run, onSelect }: Props) {
  if (!run.graph) return <div style={{ color: "#98a1b3" }}>No graph available.</div>;
  const statuses = statusByNode(run.steps);
  const layout = layoutDag(run.graph, statuses);
  const position = new Map(layout.nodes.map((n) => [n.id, n]));

  return (
    <svg
      width={layout.width}
      height={layout.height}
      role="img"
      aria-label="workflow DAG"
      data-testid="dag"
    >
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#3a4150" />
        </marker>
      </defs>
      {layout.edges.map((edge, i) => {
        const a = position.get(edge.from);
        const b = position.get(edge.to);
        if (!a || !b) return null;
        return (
          <g key={`${edge.from}-${edge.to}-${i}`}>
            <line
              x1={a.x + 160}
              y1={a.y + 22}
              x2={b.x}
              y2={b.y + 22}
              stroke="#3a4150"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
            {edge.label ? (
              <text x={(a.x + 160 + b.x) / 2} y={(a.y + b.y) / 2 + 14} fill="#98a1b3" fontSize={10}>
                {edge.label}
              </text>
            ) : null}
          </g>
        );
      })}
      {layout.nodes.map((node) => {
        const color = COLORS[node.status];
        return (
          <g
            key={node.id}
            transform={`translate(${node.x},${node.y})`}
            onClick={() => onSelect(node.id)}
            style={{ cursor: "pointer" }}
          >
            <rect width={160} height={44} rx={8} fill={color.fill} stroke={color.stroke} strokeWidth={1.5} />
            <text x={10} y={18} fill="#e6e8ee" fontSize={12} fontWeight={600}>
              {node.id}
            </text>
            <text x={10} y={34} fill="#98a1b3" fontSize={10}>
              {node.capability} · {node.status}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
