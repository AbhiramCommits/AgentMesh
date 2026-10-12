"""Read-only SQL tool.

Only a single ``SELECT`` (or ``WITH ... SELECT``) statement is permitted; every
mutating or multi-statement input is rejected before it reaches the database. A
hard ``LIMIT`` cap bounds the number of rows returned.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from agentmesh.tools.base import ToolContext, ToolExecutionError, ToolSpec

MAX_ROWS = 100

_DENY_WORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "vacuum",
    "analyze",
    "merge",
    "call",
    "do",
    "execute",
    "attach",
    "detach",
    "pragma",
    "set",
    "commit",
    "rollback",
    "begin",
    "lock",
    "reindex",
    "cluster",
    "refresh",
    "into",
}

_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


class SqlQueryArgs(BaseModel):
    """Arguments for :func:`sql_query`."""

    query: str = Field(..., description="A single read-only SELECT statement")


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def validate_select(query: str) -> str:
    """Return a validated statement or raise :class:`ValueError`."""

    cleaned = _strip_comments(query).strip()
    if not cleaned:
        raise ValueError("empty query")
    body = cleaned.rstrip(";").strip()
    if ";" in body:
        raise ValueError("only a single statement is allowed")
    first = body.split(None, 1)[0].lower()
    if first not in {"select", "with"}:
        raise ValueError("only SELECT statements are allowed")
    lowered = body.lower()
    for word in _DENY_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            raise ValueError(f"keyword '{word}' is not allowed in read-only queries")
    return body


def apply_limit(query: str, cap: int = MAX_ROWS) -> str:
    """Enforce a hard row cap, lowering an oversized explicit LIMIT."""

    match = _LIMIT_RE.search(query)
    if match is None:
        return f"{query} LIMIT {cap}"
    limit = int(match.group(1))
    if limit <= cap:
        return query
    return _LIMIT_RE.sub(f"LIMIT {cap}", query, count=1)


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


async def sql_query(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    """Execute a validated, capped, read-only query against Postgres."""

    params = args if isinstance(args, SqlQueryArgs) else SqlQueryArgs.model_validate(args)
    try:
        safe = validate_select(params.query)
    except ValueError as exc:
        raise ToolExecutionError("sql_query", str(exc)) from exc
    capped = apply_limit(safe)

    from agentmesh.db import get_engine

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(capped))
            columns = list(result.keys())
            rows = [
                {col: _coerce(val) for col, val in zip(columns, row, strict=False)}
                for row in result.fetchall()
            ]
    except ToolExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolExecutionError("sql_query", str(exc)) from exc

    return {"columns": columns, "rows": rows, "row_count": len(rows)}


SQL_QUERY_SPEC = ToolSpec(
    name="sql_query",
    description="Run a single read-only SELECT query against the support_tickets demo database.",
    args_model=SqlQueryArgs,
    handler=sql_query,
    timeout_s=15.0,
    idempotent=True,
    tags=("sql", "read-only"),
)
