"""File sandbox tool: read/write/list confined to a per-run directory.

Every path is resolved and checked to stay inside the sandbox root, so path
traversal (``..``) and absolute paths outside the root are rejected. The write
operation declares a compensating action that deletes the file.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentmesh.tools.base import ToolContext, ToolExecutionError, ToolSpec


class FileSandboxArgs(BaseModel):
    """Arguments for :func:`file_sandbox`."""

    op: Literal["write", "read", "list"] = Field(..., description="Operation to perform")
    path: str = Field(default=".", description="Path relative to the sandbox root")
    content: str | None = Field(default=None, description="Content for the write op")


def resolve_sandbox_root(ctx: ToolContext) -> Path:
    """Return (and create) the sandbox root for the current invocation."""

    if ctx.sandbox_dir is not None:
        root = ctx.sandbox_dir
    else:
        from agentmesh.config import get_settings

        base = get_settings().sandbox_root
        tag = str(ctx.run_id or ctx.step_id or uuid.uuid4())
        root = Path(base) if base else Path(tempfile.gettempdir()) / "agentmesh"
        root = root / tag
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_path(root: Path, relative: str) -> Path:
    if os.path.isabs(relative):
        raise ToolExecutionError("file_sandbox", "absolute paths are not allowed")
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolExecutionError("file_sandbox", "path escapes the sandbox root")
    return candidate


async def file_sandbox(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    """Perform a sandboxed read, write, or list."""

    params = (
        args if isinstance(args, FileSandboxArgs) else FileSandboxArgs.model_validate(args)
    )
    root = resolve_sandbox_root(ctx)
    try:
        target = _safe_path(root, params.path)
    except ToolExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolExecutionError("file_sandbox", str(exc)) from exc

    if params.op == "write":
        if params.content is None:
            raise ToolExecutionError("file_sandbox", "write requires 'content'")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(params.content, encoding="utf-8")
        return {
            "op": "write",
            "path": str(target.relative_to(root)),
            "bytes": len(params.content.encode("utf-8")),
        }

    if params.op == "read":
        if not target.is_file():
            raise ToolExecutionError("file_sandbox", f"no such file: {params.path}")
        return {
            "op": "read",
            "path": str(target.relative_to(root)),
            "content": target.read_text(encoding="utf-8"),
        }

    if not target.is_dir():
        raise ToolExecutionError("file_sandbox", f"no such directory: {params.path}")
    entries = sorted(p.name for p in target.iterdir())
    return {"op": "list", "path": str(target.relative_to(root)), "entries": entries}


async def _compensate_write(
    args: BaseModel | dict[str, Any], result: Any, ctx: ToolContext
) -> None:
    """Delete the file created by a prior write (compensating action)."""

    if isinstance(args, BaseModel):
        raw = args.model_dump()
    else:
        raw = dict(args)
    path = raw.get("path")
    if not path:
        return
    root = resolve_sandbox_root(ctx)
    try:
        target = _safe_path(root, path)
    except ToolExecutionError:
        return
    if target.is_file():
        target.unlink()


FILE_SANDBOX_SPEC = ToolSpec(
    name="file_sandbox",
    description="Read, write, or list files confined to a per-run sandbox directory.",
    args_model=FileSandboxArgs,
    handler=file_sandbox,
    timeout_s=10.0,
    idempotent=False,
    compensate=_compensate_write,
    tags=("filesystem", "sandbox"),
)
