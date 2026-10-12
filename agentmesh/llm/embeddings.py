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

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "do", "does", "for", "from", "had", "has", "have", "how", "if", "in",
    "into", "is", "it", "its", "may", "must", "no", "not", "of", "on", "or",
    "our", "over", "so", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "up", "use", "was", "we", "were",
    "what", "when", "which", "who", "will", "with", "within", "you", "your",
    "all", "any", "each", "more", "most", "other", "some", "such", "only",
    "own", "same", "too", "very", "just", "also", "per",
}


class EmbeddingProvider(Protocol):
    """Interface for embedding backends."""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        ...


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _tokenize_content(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


class FakeEmbedder:
    """Deterministic feature-hashing embedder (words + bigrams + char trigrams).

    Stopwords are dropped, word bigrams and character trigrams are weighted, and
    the result is L2-normalized. This gives a stable lexical similarity signal,
    so pgvector retrieval quality is measurable offline with no API key.
    """

    BIGRAM_WEIGHT = 2.0
    CHARGRAM_WEIGHT = 1.0

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize_content(text)
        features: dict[str, float] = {}
        for token in tokens:
            features[f"w:{token}"] = features.get(f"w:{token}", 0.0) + 1.0
        for a, b in zip(tokens, tokens[1:], strict=False):
            key = f"b:{a}_{b}"
            features[key] = features.get(key, 0.0) + self.BIGRAM_WEIGHT
        for token in tokens:
            padded = f"^{token}$"
            for i in range(len(padded) - 2):
                key = f"c:{padded[i : i + 3]}"
                features[key] = features.get(key, 0.0) + self.CHARGRAM_WEIGHT
        if not features:
            vec[0] = 1.0
            return vec
        for feature, weight in features.items():
            digest = int(hashlib.sha1(feature.encode("utf-8")).hexdigest(), 16)
            index = digest % self.dim
            sign = 1.0 if (digest // self.dim) % 2 == 0 else -1.0
            vec[index] += sign * math.sqrt(weight)
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
