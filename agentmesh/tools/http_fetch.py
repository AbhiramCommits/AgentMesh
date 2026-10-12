"""HTTP fetch tool with host allow-listing and a response size cap."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from agentmesh.tools.base import ToolContext, ToolExecutionError, ToolSpec

DEFAULT_ALLOWED_HOSTS: set[str] = {
    "example.com",
    "www.example.com",
    "httpbin.org",
    "api.github.com",
    "localhost",
    "127.0.0.1",
}

MAX_BYTES = 200_000

_allowed_hosts: set[str] = set(DEFAULT_ALLOWED_HOSTS)
_transport: httpx.AsyncBaseTransport | None = None


def set_allowed_hosts(hosts: set[str]) -> None:
    """Override the allow-list (used by tests)."""

    global _allowed_hosts
    _allowed_hosts = set(hosts)


def reset_allowed_hosts() -> None:
    global _allowed_hosts
    _allowed_hosts = set(DEFAULT_ALLOWED_HOSTS)


def set_http_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install a custom httpx transport (used by tests to avoid the network)."""

    global _transport
    _transport = transport


class HttpFetchArgs(BaseModel):
    """Arguments for :func:`http_fetch`."""

    url: str = Field(..., description="Absolute http(s) URL to GET")
    headers: dict[str, str] | None = Field(default=None, description="Optional request headers")


async def http_fetch(args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
    """GET an allow-listed URL, capping the number of bytes read."""

    params = args if isinstance(args, HttpFetchArgs) else HttpFetchArgs.model_validate(args)
    parsed = urlparse(params.url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolExecutionError("http_fetch", f"unsupported scheme '{parsed.scheme}'")
    if parsed.hostname not in _allowed_hosts:
        raise ToolExecutionError("http_fetch", f"host '{parsed.hostname}' is not allow-listed")

    try:
        async with httpx.AsyncClient(
            transport=_transport, timeout=10.0, follow_redirects=True
        ) as client:
            response = await client.get(params.url, headers=params.headers)
    except httpx.HTTPError as exc:
        raise ToolExecutionError("http_fetch", f"{type(exc).__name__}: {exc}") from exc

    raw = response.content
    truncated = len(raw) > MAX_BYTES
    body = raw[:MAX_BYTES]
    return {
        "status": response.status_code,
        "url": str(response.url),
        "bytes": len(body),
        "truncated": truncated,
        "text": body.decode(response.encoding or "utf-8", errors="replace"),
    }


HTTP_FETCH_SPEC = ToolSpec(
    name="http_fetch",
    description="GET a URL on an allow-listed host (max 200 KB response).",
    args_model=HttpFetchArgs,
    handler=http_fetch,
    timeout_s=12.0,
    idempotent=True,
    tags=("http", "network"),
)
