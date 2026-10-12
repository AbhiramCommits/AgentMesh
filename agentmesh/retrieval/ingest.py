"""Document ingestion: chunk, embed, and upsert into the pgvector store."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from agentmesh.db import session_scope
from agentmesh.llm.embeddings import get_embedder
from agentmesh.models import Chunk, Document

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "data" / "docs"

CHUNK_WORDS = 300
OVERLAP_WORDS = 60


def chunk_text(
    text: str, chunk_words: int = CHUNK_WORDS, overlap_words: int = OVERLAP_WORDS
) -> list[str]:
    """Split text into overlapping word windows (~400 tokens per chunk)."""

    words = text.split()
    if not words:
        return []
    step = max(1, chunk_words - overlap_words)
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + chunk_words]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_words >= len(words):
            break
    return chunks


def _title_of(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem.replace("_", " ").title()


async def ingest_text(
    session: AsyncSession, title: str, source: str, content: str
) -> Document:
    """Chunk and embed a single document within an open session."""

    document = Document(title=title, source=source)
    session.add(document)
    await session.flush()

    chunks = chunk_text(content)
    if chunks:
        vectors = await get_embedder().embed(chunks)
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            session.add(
                Chunk(
                    doc_id=document.id,
                    chunk_index=index,
                    content=chunk,
                    embedding=vector,
                )
            )
    return document


async def reset_corpus(session: AsyncSession) -> None:
    """Delete all documents and chunks (idempotent re-seeding)."""

    await session.execute(delete(Chunk))
    await session.execute(delete(Document))


async def ingest_directory(
    path: str | Path = DOCS_DIR, *, reset: bool = True
) -> dict[str, int]:
    """Ingest every ``*.md`` file in a directory into the vector store."""

    docs_path = Path(path)
    files = sorted(docs_path.glob("*.md"))
    ingested = 0
    chunks = 0
    async with session_scope() as session:
        if reset:
            await reset_corpus(session)
        for file in files:
            text = file.read_text(encoding="utf-8")
            document = await ingest_text(session, _title_of(file, text), file.name, text)
            await session.flush()
            count = len(chunk_text(text))
            chunks += count
            ingested += 1
            _ = document
    return {"documents": ingested, "chunks": chunks}
