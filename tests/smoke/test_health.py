"""Smoke tests for the engine's /health endpoint."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dap_engine.api.health import _is_db_reachable
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-health-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="health-smoke-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_health_returns_db_dialect_and_reachable_for_healthy_sqlite(
    client: TestClient,
) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_dialect"] == "sqlite"
    # The default fixture brings up a fresh SQLite DB and migrations
    # complete during the TestClient context manager — probe should
    # succeed.
    assert body["db_reachable"] is True


def test_health_reports_unreachable_when_db_engine_missing(client: TestClient) -> None:
    # Removing app.state.db_engine simulates the case where startup
    # failed partway through (or a future refactor introduces a window
    # where the field is unset).
    client.app.state.db_engine = None  # type: ignore[attr-defined]
    body = client.get("/health").json()
    assert body["db_reachable"] is False
    # Dialect attribute is independent — still surfaced.
    assert body["db_dialect"] == "sqlite"


def test_health_reports_unreachable_when_select_raises(client: TestClient) -> None:
    # Replace the engine with a mock that raises on connect() so the
    # probe's except-handler path is exercised. Reads the dialect from
    # app.state separately so /health stays self-consistent.
    broken_engine = MagicMock()
    broken_engine.connect.side_effect = RuntimeError("boom: tcp reset")
    client.app.state.db_engine = broken_engine  # type: ignore[attr-defined]

    body = client.get("/health").json()
    assert body["db_reachable"] is False


def test_is_db_reachable_returns_false_for_none_engine() -> None:
    assert _is_db_reachable(None) is False
