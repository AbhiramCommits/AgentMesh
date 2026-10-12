// Cost and latency formatting helpers.
import type { CostRollup } from "../api/types";

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export interface CostSummary {
  prompt: string;
  completion: string;
  total: string;
  latency: string;
  wallClock: string;
  toolCalls: number;
}

export function costSummary(cost: CostRollup | null): CostSummary {
  const empty: CostRollup = {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    latency_ms: 0,
    tool_calls: 0,
    wall_clock_ms: null,
  };
  const value = cost ?? empty;
  return {
    prompt: formatTokens(value.prompt_tokens),
    completion: formatTokens(value.completion_tokens),
    total: formatTokens(value.total_tokens),
    latency: formatMs(value.latency_ms),
    wallClock: formatMs(value.wall_clock_ms),
    toolCalls: value.tool_calls,
  };
}
