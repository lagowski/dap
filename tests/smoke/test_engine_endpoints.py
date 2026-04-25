"""Smoke test — engine app factory + endpointy. Bez startu serwera (FastAPI TestClient)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    tmp = tempfile.mkdtemp(prefix="dap-smoke-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "dap-engine"
    assert body["version"] == "0.0.1"
    assert "timestamp" in body


def test_runtimes_list(client: TestClient) -> None:
    response = client.get("/runtimes")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    ids = {a["id"] for a in body}
    assert ids == {"bash", "http", "api-call", "claude-code", "gemini-cli", "codex", "aider"}
    for adapter in body:
        assert "displayName" in adapter
        assert adapter["kind"] in {"cli", "api", "shell", "http"}


def test_runtime_health_existing(client: TestClient) -> None:
    response = client.get("/runtimes/bash/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True


def test_runtime_health_missing_binary(client: TestClient) -> None:
    response = client.get("/runtimes/claude-code/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "missing" in body


def test_runtime_health_unknown_id(client: TestClient) -> None:
    response = client.get("/runtimes/nonexistent/health")
    assert response.status_code == 404
