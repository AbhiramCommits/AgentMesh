"""Model provider interface plus real and deterministic fake implementations.

The fake provider is fully deterministic and offline: agents tag their system
prompt with a task marker (``[PLAN]``, ``[ANALYZE]``, ``[VERIFY]``) and the fake
returns a stable, structured response, so the whole repo runs without an API key.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

ChatMessage = dict[str, str]


class ModelResponse(BaseModel):
    """Normalized model completion."""

    text: str = ""
    model: str = "fake"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ModelProvider(Protocol):
    """Interface every model backend implements."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResponse:  # pragma: no cover
        ...


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate (~4 chars/token)."""

    return max(1, len(text) // 4)


class FakeModelProvider:
    """Deterministic offline provider used by tests, demos, and benchmarks."""

    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.calls = 0

    def _respond(self, text: str, tool_calls: list[dict[str, Any]] | None = None) -> ModelResponse:
        self.calls += 1
        prompt = self.model
        return ModelResponse(
            text=text,
            model=prompt,
            prompt_tokens=estimate_tokens(text) + 16,
            completion_tokens=estimate_tokens(text),
            tool_calls=tool_calls or [],
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        blob = "\n".join(m.get("content", "") for m in messages)
        chosen = model or self.model
        self.model = chosen

        if "[PLAN]" in blob:
            request = _after(blob, "REQUEST:", "plan")
            steps = [
                f"Retrieve policies and ticket context for: {request}",
                "Analyze the retrieved evidence and ticket data",
                "Draft an answer grounded in the evidence",
                "Verify the draft against the evidence",
            ]
            import json

            return self._respond(json.dumps({"plan": steps, "request": request}))

        if "[ANALYZE]" in blob:
            if "TOOL_RESULT" not in blob:
                call = {
                    "id": "call_1",
                    "name": "sql_query",
                    "arguments": {
                        "query": (
                            "SELECT category, COUNT(*) AS open_tickets "
                            "FROM support_tickets WHERE status = 'open' "
                            "GROUP BY category ORDER BY open_tickets DESC"
                        )
                    },
                }
                return self._respond("I will query the ticket database.", [call])
            request = _after(blob, "REQUEST:", "the request")
            return self._respond(
                f"Based on the retrieved evidence and ticket data, {request}. "
                "Open tickets cluster in the categories returned by the query."
            )

        if "[VERIFY]" in blob:
            import json

            revise_once = "revise_once" in blob
            revision = _int_after(blob, "revision_index:", 0)
            if revise_once and revision == 0:
                verdict = "revise"
                reasons = ["Draft lacks an explicit citation for the pricing tier."]
            else:
                verdict = "pass"
                reasons = ["Answer is supported by the retrieved chunks."]
            return self._respond(json.dumps({"verdict": verdict, "reasons": reasons}))

        return self._respond(f"ack: {blob[:60]}")


def _after(blob: str, marker: str, default: str) -> str:
    if marker in blob:
        tail = blob.split(marker, 1)[1].strip()
        line = tail.splitlines()[0].strip()
        return line or default
    return default


def _int_after(blob: str, marker: str, default: int) -> int:
    if marker in blob:
        tail = blob.split(marker, 1)[1].strip()
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        if digits:
            return int(digits)
    return default


class OpenAIProvider:
    """OpenAI-compatible chat provider (works with any compatible endpoint)."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self.model = model

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        chosen = model or self.model
        kwargs: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]

        completion = await self._client.chat.completions.create(**kwargs)
        choice = completion.choices[0].message
        calls: list[dict[str, Any]] = []
        for call in choice.tool_calls or []:
            import json

            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append({"id": call.id, "name": call.function.name, "arguments": arguments})
        usage = completion.usage
        return ModelResponse(
            text=choice.content or "",
            model=chosen,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            tool_calls=calls,
        )
