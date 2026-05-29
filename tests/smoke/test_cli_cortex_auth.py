"""CLI → engine auth: ``dap project …`` must send ``Authorization: Bearer``.

The engine requires auth (every endpoint except ``/health`` 401s without a
bearer token — JWT or opaque ``dap_*`` API token). The CLI funnels all engine
calls through ``cortex._client``; it previously sent no header, so every
``dap project run/state/approve/reject`` 401'd against its own engine.

These unit tests pin the resolution contract on ``_client``:
token argument > ``DAP_AUTH_TOKEN`` env > no header.
"""

from __future__ import annotations

import httpx
import pytest
from dap_cli.commands import cortex


def _auth(client: httpx.Client) -> str | None:
    # httpx normalises header names to lower-case; missing → None.
    value: str | None = client.headers.get("authorization")
    return value


def test_client_attaches_bearer_from_token_arg() -> None:
    with cortex._client("http://localhost:7333", token="dap_explicit") as client:
        assert _auth(client) == "Bearer dap_explicit"


def test_client_attaches_bearer_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAP_AUTH_TOKEN", "dap_from_env")
    with cortex._client("http://localhost:7333") as client:
        assert _auth(client) == "Bearer dap_from_env"


def test_client_token_arg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAP_AUTH_TOKEN", "dap_from_env")
    with cortex._client("http://localhost:7333", token="dap_explicit") as client:
        assert _auth(client) == "Bearer dap_explicit"


def test_client_no_header_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    with cortex._client("http://localhost:7333") as client:
        assert _auth(client) is None


def test_client_blank_token_sends_no_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace / empty token must not produce a bogus ``Bearer`` header."""
    monkeypatch.setenv("DAP_AUTH_TOKEN", "   ")
    with cortex._client("http://localhost:7333") as client:
        assert _auth(client) is None


def test_client_strips_token_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    with cortex._client("http://localhost:7333", token="  dap_padded  ") as client:
        assert _auth(client) == "Bearer dap_padded"


# ---------------------------------------------------------------------------
# --token flag propagation: the entrypoints export an explicit token to
# DAP_AUTH_TOKEN so the many internal helpers (which build their own client
# and resolve from the env) pick it up.
# ---------------------------------------------------------------------------


def test_export_token_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    cortex._export_token_to_env("dap_flag")
    # A subsequent helper-style client (no explicit token) now authenticates.
    with cortex._client("http://localhost:7333") as client:
        assert _auth(client) == "Bearer dap_flag"


def test_export_token_noop_for_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    cortex._export_token_to_env("   ")
    assert "DAP_AUTH_TOKEN" not in __import__("os").environ
    cortex._export_token_to_env(None)
    assert "DAP_AUTH_TOKEN" not in __import__("os").environ


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
