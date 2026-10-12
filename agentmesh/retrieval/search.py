"""pgvector similarity search over the document store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentmesh.llm.embeddings import get_embedder


@dataclass
class SearchHit:
    """A single retrieved chunk."""

    doc_id: str
    chunk_id: str
    content: str
    score: float
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "content": self.content,
            "score": self.score,
            "title": self.title,
        }


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


async def search(
    query: str,
    k: int = 5,
    *,
    session: AsyncSession | None = None,
) -> list[SearchHit]:
    """Return the top-k chunks by cosine similarity to the query."""

    embedder = get_embedder()
    vectors = await embedder.embed([query])
    literal = _vector_literal(vectors[0])

    sql = text(
        """
        SELECT c.id AS chunk_id,
               c.doc_id AS doc_id,
               c.content AS content,
               d.title AS title,
               1 - (c.embedding <=> CAST(:vec AS vector)) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.doc_id
        ORDER BY c.embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )

    async def _run(sess: AsyncSession) -> list[SearchHit]:
        rows = (await sess.execute(sql, {"vec": literal, "k": k})).mappings().all()
        return [
            SearchHit(
                doc_id=str(row["doc_id"]),
                chunk_id=str(row["chunk_id"]),
                content=row["content"],
                score=float(row["score"]),
                title=row["title"],
            )
            for row in rows
        ]

    if session is not None:
        return await _run(session)
    from agentmesh.db import session_scope

    async with session_scope() as own:
        return await _run(own)
