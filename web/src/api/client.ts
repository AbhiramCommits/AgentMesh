// Typed API client. The base URL is configurable via VITE_API_BASE.
import type {
  Escalation,
  Run,
  RunDetail,
  ToolDescriptor,
  WorkflowSummary,
} from "./types";

const BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export interface StartRunPayload {
  workflow_name: string;
  input: Record<string, unknown>;
  attempt_budget?: number;
  crash_at_node?: string | null;
}

export const api = {
  health: () => request<{ status: string; version: string; provider: string }>("/health"),
  listWorkflows: () => request<WorkflowSummary[]>("/workflows"),
  listTools: () => request<{ tools: ToolDescriptor[] }>("/tools"),
  listRuns: (limit = 50) => request<Run[]>(`/runs?limit=${limit}`),
  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),
  startRun: (payload: StartRunPayload) =>
    request<Run>("/runs", { method: "POST", body: JSON.stringify(payload) }),
  resumeRun: (id: string) =>
    request<RunDetail>(`/runs/${id}/resume`, { method: "POST" }),
  replayRun: (id: string, fromNode: string) =>
    request<Run>(
      `/runs/${id}/replay?from_node=${encodeURIComponent(fromNode)}`,
      { method: "POST" },
    ),
  listEscalations: (status?: string) =>
    request<Escalation[]>(`/escalations${status ? `?status=${status}` : ""}`),
  resolveEscalation: (id: string, resolution: string) =>
    request<Escalation>(`/escalations/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ resolution }),
    }),
  eventsUrl: (id: string) => `${BASE}/runs/${id}/events`,
};

export { BASE };
