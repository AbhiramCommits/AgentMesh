"""SQLAlchemy 2.x ORM models for the AgentMesh durable state."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentmesh.config import get_settings
from agentmesh.db import Base

EMBEDDING_DIM = get_settings().embedding_dim


class RunStatus(enum.StrEnum):
    """Lifecycle states for a workflow run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_HUMAN = "awaiting_human"


class StepStatus(enum.StrEnum):
    """Lifecycle states for a single workflow node execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EscalationStatus(enum.StrEnum):
    """Lifecycle states for a human-review escalation."""

    OPEN = "open"
    RESOLVED = "resolved"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Run(Base):
    """A single execution of a declarative workflow graph."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RunStatus.PENDING.value, index=True
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempt_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    steps: Mapped[list[Step]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Step.created_at"
    )
    checkpoints: Mapped[list[Checkpoint]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    escalations: Mapped[list[Escalation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Step(Base):
    """One node execution within a run."""

    __tablename__ = "steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StepStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="steps")
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )
    retrievals: Mapped[list[Retrieval]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )


class ToolCall(Base):
    """A single recorded tool invocation, including failures/timeouts."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    step: Mapped[Step] = relationship(back_populates="tool_calls")


class Checkpoint(Base):
    """A committed snapshot of run state after a node completes."""

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="checkpoints")


class Retrieval(Base):
    """Chunk-level provenance for a step grounded in the document store."""

    __tablename__ = "retrievals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), index=True
    )
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chunk_text: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    step: Mapped[Step] = relationship(back_populates="retrievals")


class Escalation(Base):
    """A human-review request raised when a node exhausts its recovery budget."""

    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("steps.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EscalationStatus.OPEN.value, index=True
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="escalations")


class Document(Base):
    """A source document in the retrieval corpus."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    source: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A retrieved chunk with a pgvector embedding."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")
