// Types mirroring the FastAPI response schemas.

export type RunStatus = "pending" | "running" | "completed" | "failed" | "awaiting_human";
export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface CostRollup {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  tool_calls: number;
  wall_clock_ms: number | null;
}

export interface ToolCall {
  id: string;
  tool_name: string;
  args: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  ok: boolean;
  error: string | null;
  latency_ms: number;
  timed_out: boolean;
  created_at: string;
}

export interface Retrieval {
  id: string;
  doc_id: string | null;
  chunk_id: string | null;
  chunk_text: string | null;
  score: number;
}

export interface Step {
  id: string;
  node_id: string;
  agent_name: string | null;
  status: StepStatus;
  attempt: number;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  error: string | null;
  model_used: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  tool_calls: ToolCall[];
  retrievals: Retrieval[];
}

export interface Escalation {
  id: string;
  run_id: string;
  step_id: string | null;
  reason: string;
  status: string;
  resolution: string | null;
  created_at: string;
}

export interface GraphNode {
  id: string;
  capability: string;
  agent: string | null;
  description: string;
  next: string[];
  conditional: Record<string, string>;
  models: string[];
  loop_limit: number;
}

export interface WorkflowGraph {
  name: string;
  description: string;
  start: string;
  max_iterations: number | null;
  nodes: GraphNode[];
}

export interface Run {
  id: string;
  workflow_name: string;
  status: RunStatus;
  input: Record<string, unknown>;
  attempt_budget: number;
  created_at: string;
  updated_at: string;
  cost: CostRollup | null;
}

export interface RunDetail extends Run {
  steps: Step[];
  escalations: Escalation[];
  graph: WorkflowGraph | null;
}

export interface ToolDescriptor {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface WorkflowSummary {
  name: string;
  description: string;
  nodes: number;
}
