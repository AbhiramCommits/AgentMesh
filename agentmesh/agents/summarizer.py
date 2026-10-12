"""Summarizer agent: condenses the grounded answer into a final summary."""

from __future__ import annotations

import time

from agentmesh.agents.base import RunContext, StepResult


class SummarizerAgent:
    """Produces a concise final summary from the latest answer and evidence."""

    name = "summarizer"
    capabilities: tuple[str, ...] = ("summarize",)

    async def run(self, ctx: RunContext) -> StepResult:
        start = time.perf_counter()
        answer = ctx.latest_answer()
        evidence = ctx.evidence()
        sources = ", ".join(sorted({str(e.get("title", "doc")) for e in evidence})) or "none"
        messages = [
            {
                "role": "system",
                "content": (
                    "[SUMMARIZE] Condense the answer into an executive summary and "
                    "list the source documents used."
                ),
            },
            {"role": "user", "content": f"ANSWER: {answer}\nSOURCES: {sources}"},
        ]
        response = await ctx.provider.complete(messages, model=ctx.model)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            output={"summary": response.text, "sources": sources, "answer": answer},
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            model_used=response.model,
            latency_ms=latency_ms,
        )
