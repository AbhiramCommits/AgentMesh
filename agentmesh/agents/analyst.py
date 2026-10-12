"""Analyst agent: bounded tool-calling loop over the shared registry."""

from __future__ import annotations

import json
import time

from agentmesh.agents.base import RunContext, StepResult
from agentmesh.tools.base import ToolContext, ToolError

MAX_TURNS = 4


class AnalystAgent:
    """Iteratively calls tools until the model produces a final answer."""

    name = "analyst"
    capabilities: tuple[str, ...] = ("analyze", "revise")

    async def run(self, ctx: RunContext) -> StepResult:
        start = time.perf_counter()
        request = str(ctx.run_input.get("request", "")).strip()
        evidence = ctx.evidence()
        evidence_text = "\n".join(
            f"- [{e.get('title', 'doc')}] {e.get('content', '')[:400]}" for e in evidence[:5]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "[ANALYZE] You are an analyst with access to tools. "
                    "Call tools as needed, then produce a grounded final answer."
                ),
            },
            {
                "role": "user",
                "content": f"REQUEST: {request}\n\nEVIDENCE:\n{evidence_text}",
            },
        ]

        prompt_tokens = 0
        completion_tokens = 0
        model_used = ctx.model
        used_tools = 0

        for turn in range(1, MAX_TURNS + 1):
            response = await ctx.provider.complete(
                messages,
                model=ctx.model,
                tools=ctx.registry.list_descriptors(),
            )
            prompt_tokens += response.prompt_tokens
            completion_tokens += response.completion_tokens
            model_used = response.model

            if response.tool_calls:
                messages.append(
                    {"role": "assistant", "content": response.text or "calling tool"}
                )
                for call in response.tool_calls:
                    used_tools += 1
                    try:
                        result = await ctx.registry.invoke(
                            call["name"],
                            call.get("arguments", {}),
                            step_id=ctx.step_id,
                            ctx=ToolContext(
                                run_id=ctx.run_id,
                                step_id=ctx.step_id,
                                sandbox_dir=ctx.sandbox_dir,
                            ),
                        )
                    except ToolError as exc:
                        result = {"error": str(exc)}
                    messages.append(
                        {
                            "role": "tool",
                            "content": f"TOOL_RESULT {call['name']}: "
                            f"{json.dumps(result, default=str)[:2000]}",
                        }
                    )
                continue

            latency_ms = int((time.perf_counter() - start) * 1000)
            return StepResult(
                output={
                    "answer": response.text,
                    "turns": turn,
                    "tool_calls": used_tools,
                },
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model_used=model_used,
                latency_ms=latency_ms,
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            output={"answer": "", "turns": MAX_TURNS, "truncated": True, "tool_calls": used_tools},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_used=model_used,
            latency_ms=latency_ms,
        )
