"""Smoke test — engine app factory + endpointy. Bez startu serwera (FastAPI TestClient)."""

from __future__ import annotations

from unittest.mock import patch

from dap_engine.version import __version__
from fastapi.testclient import TestClient

# The ``client`` fixture lives in ``tests/smoke/conftest.py``. The shared
# fixture adds an ``auth_jwt_secret`` default — these tests don't hit
# the auth subsystem, but the extra config field is harmless.


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "dap-engine"
    assert body["version"] == __version__
    assert body["db_dialect"] == "sqlite"
    assert "timestamp" in body


def test_runtimes_list(client: TestClient) -> None:
    response = client.get("/runtimes")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    ids = {a["id"] for a in body}
    assert ids == {
        "bash",
        "http",
        "api-call",
        "claude-code",
        "gemini-cli",
        "codex",
        "aider",
        "python-func",
    }
    for adapter in body:
        assert "displayName" in adapter
        assert adapter["kind"] in {"cli", "api", "shell", "http"}


def test_runtime_health_existing(client: TestClient) -> None:
    response = client.get("/runtimes/bash/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True


def test_runtime_health_missing_binary(client: TestClient) -> None:
    """Probe must report unavailable + a ``missing`` hint when the binary
    isn't on PATH. ``shutil.which`` is patched so this is deterministic
    on dev boxes that happen to have ``claude`` installed."""
    with patch("dap_runtimes.adapters._cli_base.shutil.which", return_value=None):
        response = client.get("/runtimes/claude-code/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "missing" in body


def test_runtime_health_unknown_id(client: TestClient) -> None:
    response = client.get("/runtimes/nonexistent/health")
    assert response.status_code == 404
