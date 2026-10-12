import { describe, expect, it } from "vitest";
import { costSummary, formatMs, formatTokens } from "./cost";
import type { CostRollup } from "../api/types";

describe("formatTokens", () => {
  it("formats small and large token counts", () => {
    expect(formatTokens(42)).toBe("42");
    expect(formatTokens(1500)).toBe("1.5k");
    expect(formatTokens(2_500_000)).toBe("2.50M");
  });
});

describe("formatMs", () => {
  it("formats milliseconds and seconds", () => {
    expect(formatMs(500)).toBe("500 ms");
    expect(formatMs(2500)).toBe("2.50 s");
    expect(formatMs(null)).toBe("—");
  });
});

describe("costSummary", () => {
  it("rolls up a cost object", () => {
    const cost: CostRollup = {
      prompt_tokens: 1200,
      completion_tokens: 300,
      total_tokens: 1500,
      latency_ms: 2500,
      tool_calls: 2,
      wall_clock_ms: 4000,
    };
    const summary = costSummary(cost);
    expect(summary.prompt).toBe("1.2k");
    expect(summary.completion).toBe("300");
    expect(summary.total).toBe("1.5k");
    expect(summary.latency).toBe("2.50 s");
    expect(summary.wallClock).toBe("4.00 s");
    expect(summary.toolCalls).toBe(2);
  });

  it("handles a null cost", () => {
    const summary = costSummary(null);
    expect(summary.total).toBe("0");
    expect(summary.wallClock).toBe("—");
  });
});
