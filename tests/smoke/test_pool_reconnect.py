"""Smoke tests for pool reconnect_timeout against a live PostgreSQL (#580).

Requires ``DAP_DATABASE_URL`` pointing to a provisioned ``dap`` database.
Skipped automatically when the env var is absent.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.db import detect_dialect
from fastapi.testclient import TestClient

PG_URL: str | None = os.environ.get("DAP_DATABASE_URL")
_PG_AVAILABLE = PG_URL is not None and detect_dialect(PG_URL) == "postgresql"

pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE,
    reason="DAP_DATABASE_URL not set or not a postgresql:// URL — skipping PostgreSQL pool tests",
)


@pytest.fixture()
def pg_client() -> Iterator[TestClient]:
    assert PG_URL is not None
    cfg = EngineConfig(database_url=PG_URL, pg_pool_reconnect_timeout=30.0)
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
class TestPoolReconnectSmoke:
    def test_health_returns_pool_stats(self, pg_client: TestClient) -> None:
        resp = pg_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "pool_size" in data
        assert "pool_available" in data
        assert "pool_exhausted" in data
        assert data["db_reachable"] is True

    def test_reconnect_timeout_accepted(self, pg_client: TestClient) -> None:
        """Verify the pool accepted reconnect_timeout without error."""
        resp = pg_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["db_dialect"] == "postgresql"
