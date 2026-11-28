"""Shared httpx client factory for CLI → engine calls (#778 Phase 1).

Consolidates the two near-identical factories that lived in
``commands/cortex.py`` (``_client`` for short-lived requests,
``_events_client`` for the long-lived SSE stream) into one
parameterized ``make_client``.
"""

from __future__ import annotations

import os

import httpx

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "auth_headers",
    "make_client",
    "resolve_auth_token",
]

DEFAULT_TIMEOUT_S = 10.0


def resolve_auth_token(token: str | None = None) -> str:
    """Resolve the engine bearer token: explicit arg wins, else ``DAP_AUTH_TOKEN``.

    Returns ``""`` when neither is set (or only whitespace) — the engine's
    ``/health`` is public, but every other endpoint 401s, so a missing token
    surfaces as a clear 401 the caller already handles.
    """
    return (token or os.environ.get("DAP_AUTH_TOKEN") or "").strip()


def auth_headers(token: str | None = None) -> dict[str, str]:
    """Build the ``Authorization: Bearer`` header dict (empty when no token).

    The engine accepts a JWT or an opaque ``dap_*`` API token
    (``/auth/api-tokens``); the CLI forwards whatever is configured via
    ``--token`` / ``DAP_AUTH_TOKEN`` (#662 Phase 3d).
    """
    resolved = resolve_auth_token(token)
    return {"Authorization": f"Bearer {resolved}"} if resolved else {}


def make_client(
    engine_url: str,
    token: str | None = None,
    *,
    streaming: bool = False,
) -> httpx.Client:
    """HTTP client for the engine, carrying ``Authorization: Bearer`` when a
    token is available.

    ``streaming=True`` disables the **read timeout** for the long-lived SSE
    events stream (``--follow``) so a quiet stream (e.g. a 16-min cortex node
    producing no output) is not aborted after the default 10 s.
    Connect/write/pool timeouts stay bounded so a dead engine still fails
    fast rather than hanging forever (#662 Phase 3d).
    """
    if streaming:
        timeout = httpx.Timeout(
            connect=DEFAULT_TIMEOUT_S, read=None, write=DEFAULT_TIMEOUT_S, pool=DEFAULT_TIMEOUT_S
        )
    else:
        timeout = httpx.Timeout(DEFAULT_TIMEOUT_S)
    return httpx.Client(
        base_url=engine_url.rstrip("/"),
        timeout=timeout,
        headers=auth_headers(token),
    )
