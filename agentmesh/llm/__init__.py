"""LLM layer: model and embedding providers."""

from __future__ import annotations

from agentmesh.config import get_settings
from agentmesh.llm.embeddings import (
    EmbeddingProvider,
    FakeEmbedder,
    OpenAIEmbedder,
    get_embedder,
)
from agentmesh.llm.provider import (
    ChatMessage,
    FakeModelProvider,
    ModelProvider,
    ModelResponse,
    OpenAIProvider,
    estimate_tokens,
)

__all__ = [
    "ChatMessage",
    "EmbeddingProvider",
    "FakeEmbedder",
    "FakeModelProvider",
    "ModelProvider",
    "ModelResponse",
    "OpenAIEmbedder",
    "OpenAIProvider",
    "estimate_tokens",
    "get_embedder",
    "get_provider",
]


def get_provider() -> ModelProvider:
    """Return the configured model provider (fake unless opted in)."""

    settings = get_settings()
    if settings.use_fake_provider:
        return FakeModelProvider(model=settings.model)
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.model,
    )
