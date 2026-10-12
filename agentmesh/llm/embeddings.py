"""Embedding providers: deterministic hashing fake and OpenAI-compatible real.

The fake embedder uses signed feature hashing over word unigrams and bigrams and
L2-normalizes the result. That yields a genuine lexical similarity structure, so
pgvector retrieval quality is measurable offline with no API key.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from agentmesh.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    """Interface for embedding backends."""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        ...


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class FakeEmbedder:
    """Deterministic 256-dim feature-hashing embedder."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        features = list(tokens) + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        if not features:
            vec[0] = 1.0
            return vec
        for feature in features:
            digest = int(hashlib.sha1(feature.encode("utf-8")).hexdigest(), 16)
            index = digest % self.dim
            sign = 1.0 if (digest // self.dim) % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]


class OpenAIEmbedder:
    """OpenAI-compatible embedding provider (requests ``dim`` dimensions)."""

    def __init__(self, api_key: str, base_url: str, model: str, dim: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dim
        )
        return [item.embedding for item in response.data]


def get_embedder() -> EmbeddingProvider:
    """Return the configured embedding provider."""

    settings = get_settings()
    if settings.use_fake_provider:
        return FakeEmbedder(dim=settings.embedding_dim)
    return OpenAIEmbedder(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model="text-embedding-3-small",
        dim=settings.embedding_dim,
    )
