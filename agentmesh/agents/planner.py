"""Planner agent: decomposes the request into an ordered step plan."""

from __future__ import annotations

import time

from agentmesh.agents.base import RunContext, StepResult, extract_json


class PlannerAgent:
    """Asks the model to decompose the request into a short plan."""

    name = "planner"
    capabilities: tuple[str, ...] = ("plan",)

    async def run(self, ctx: RunContext) -> StepResult:
        request = str(ctx.run_input.get("request", "")).strip()
        start = time.perf_counter()
        messages = [
            {
                "role": "system",
                "content": (
                    "[PLAN] You are the planning agent in a workflow orchestrator. "
                    "Decompose the request into an ordered list of sub-tasks."
                ),
            },
            {"role": "user", "content": f"REQUEST: {request}"},
        ]
        response = await ctx.provider.complete(messages, model=ctx.model)
        data = extract_json(response.text) or {}
        plan = data.get("plan") or [
            "Retrieve relevant policies",
            "Analyze ticket data",
            "Draft an answer",
            "Verify the answer",
        ]
        latency_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            output={"plan": plan, "request": request},
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            model_used=response.model,
            latency_ms=latency_ms,
        )
