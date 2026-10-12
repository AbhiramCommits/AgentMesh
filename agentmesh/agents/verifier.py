"""Verifier agent: checks the draft answer against retrieved evidence."""

from __future__ import annotations

import time

from agentmesh.agents.base import RunContext, StepResult, extract_json

VALID_VERDICTS = {"pass", "revise", "fail"}


class VerifierAgent:
    """Returns a pass/revise/fail verdict with reasons."""

    name = "verifier"
    capabilities: tuple[str, ...] = ("verify",)
    default_model = "fake-verifier"

    async def run(self, ctx: RunContext) -> StepResult:
        start = time.perf_counter()
        draft = ctx.latest_answer()
        evidence = ctx.evidence()
        evidence_text = "\n".join(
            f"- [{e.get('title', 'doc')}] {e.get('content', '')[:300]}" for e in evidence[:5]
        )
        revision = ctx.iterations.get(ctx.node_id, 0)
        revise_once = "revise_once" if ctx.node.params.get("revise_once") else ""
        messages = [
            {
                "role": "system",
                "content": (
                    "[VERIFY] You verify a draft answer against retrieved evidence. "
                    f"revision_index:{revision} {revise_once}".strip()
                ),
            },
            {
                "role": "user",
                "content": f"DRAFT:\n{draft}\n\nEVIDENCE:\n{evidence_text}",
            },
        ]
        response = await ctx.provider.complete(messages, model=ctx.model)
        data = extract_json(response.text) or {}
        verdict = str(data.get("verdict", "pass")).lower()
        if verdict not in VALID_VERDICTS:
            verdict = "pass"
        reasons = data.get("reasons") or []
        latency_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            output={"verdict": verdict, "reasons": reasons, "checked_draft": draft[:500]},
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            model_used=response.model,
            latency_ms=latency_ms,
            verdict=verdict,
        )
