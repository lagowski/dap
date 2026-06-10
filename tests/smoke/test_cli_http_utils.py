"""Unit tests for the consolidated CLI HTTP client factory (#778 Phase 1).

``dap_cli.http_utils.make_client`` replaces the two near-identical
factories that lived in ``commands/cortex.py`` (``_client`` /
``_events_client``). The auth-header behaviour itself is covered in
``test_cli_cortex_auth.py`` against the cortex-module aliases; here we
pin down the factory's timeout shapes and base-URL normalisation.
"""

from __future__ import annotations

import httpx
import pytest

from dap_cli.http_utils import auth_headers, make_client, resolve_auth_token


def test_make_client_default_timeouts() -> None:
    """Short-lived request client: 10 s across connect/read/write/pool."""
    with make_client("http://localhost:7333") as client:
        assert client.timeout == httpx.Timeout(10.0)


def test_make_client_streaming_disables_read_timeout() -> None:
    """SSE stream client: read timeout off, the rest stays bounded so a
    dead engine still fails fast rather than hanging forever."""
    with make_client("http://localhost:7333", streaming=True) as client:
        assert client.timeout.read is None
        assert client.timeout.connect == 10.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 10.0


def test_make_client_strips_trailing_slash_from_base_url() -> None:
    with make_client("http://localhost:7333///") as client:
        assert str(client.base_url) == "http://localhost:7333"


def test_make_client_attaches_bearer_token() -> None:
    with make_client("http://localhost:7333", token="tok-123") as client:
        assert client.headers["Authorization"] == "Bearer tok-123"


def test_make_client_no_auth_header_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    with make_client("http://localhost:7333") as client:
        assert "Authorization" not in client.headers


def test_resolve_auth_token_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAP_AUTH_TOKEN", "  env-tok  ")
    assert resolve_auth_token(None) == "env-tok"
    assert resolve_auth_token("arg-tok") == "arg-tok"


def test_auth_headers_empty_for_blank_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_AUTH_TOKEN", raising=False)
    assert auth_headers("   ") == {}
    assert auth_headers(None) == {}
    assert auth_headers("tok") == {"Authorization": "Bearer tok"}
