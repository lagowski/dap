"""Smoke tests for the engine's /health endpoint.

The ``client`` fixture comes from ``tests/smoke/conftest.py`` — a plain
``TestClient`` against a fresh engine with default test config. No
custom ``EngineConfig`` overrides needed here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from dap_engine.api.health import _is_db_reachable
from dap_engine.version import __version__
from fastapi.testclient import TestClient


def test_health_returns_db_dialect_and_reachable_for_healthy_sqlite(
    client: TestClient,
) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
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


def test_version_endpoint_returns_engine_version(client: TestClient) -> None:
    resp = client.get("/version")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "service": "dap-engine",
        "version": __version__,
    }
