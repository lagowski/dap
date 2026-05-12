"""PostgreSQL backend smoke test (#142).

Requires a provisioned ``dap`` database.  Set ``DAP_DATABASE_URL`` to a
``postgresql+asyncpg://`` connection string to run these tests; otherwise
they are automatically skipped.

Provisioning (run once, as admin):
    CREATE DATABASE dap;
    CREATE USER dap WITH PASSWORD '<password>';
    GRANT ALL PRIVILEGES ON DATABASE dap TO dap;
    \\c dap
    GRANT CREATE ON SCHEMA public TO dap;
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.db import detect_dialect, pg_conn_string
from fastapi.testclient import TestClient

PG_URL: str | None = os.environ.get("DAP_DATABASE_URL")
_PG_AVAILABLE = PG_URL is not None and detect_dialect(PG_URL) == "postgresql"

pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE,
    reason="DAP_DATABASE_URL not set or not a postgresql:// URL — skipping PostgreSQL tests",
)


@pytest.fixture
def pg_client() -> Iterator[TestClient]:
    assert PG_URL is not None
    config = EngineConfig(database_url=PG_URL)
    app = create_app(config)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        # Delete rows from application tables (reverse FK order: children
        # before parents) so re-runs against the same DB don't accumulate
        # state across test invocations. Plain DELETE rather than TRUNCATE —
        # avoids PG-specific syntax and identity-reset complexity, and the
        # row counts in smoke tests are small enough that DELETE is fine.
        # langgraph checkpoint tables are managed by the saver and stay
        # between tests.
        from dap_engine.persistence.db import _pg_sync_url
        from dap_engine.persistence.models import Base
        from sqlalchemy import create_engine

        cleanup_engine = create_engine(_pg_sync_url(PG_URL), future=True)
        try:
            with cleanup_engine.begin() as conn:
                for table in reversed(Base.metadata.sorted_tables):
                    conn.execute(table.delete())
        finally:
            cleanup_engine.dispose()


def test_health_reports_postgresql_dialect(pg_client: TestClient) -> None:
    response = pg_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db_dialect"] == "postgresql"


def test_postgresql_tables_created_on_startup(pg_client: TestClient) -> None:
    """Engine startup should create all ORM tables via create_all + apply_migrations."""
    # If the engine started successfully (fixture didn't raise), tables exist.
    # Verify via a simple API call that touches the DB.
    response = pg_client.get("/projects")
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)


def test_checkpointer_tables_created_on_startup(pg_client: TestClient) -> None:
    """checkpointer.setup() must be called in lifespan so LangGraph tables exist.

    Before fix (#173): tables were missing on a fresh DB, causing every run to
    fail immediately with ``UndefinedTable: relation "checkpoints" does not exist``.
    """
    import psycopg
    from dap_engine.persistence.db import pg_conn_string

    assert PG_URL is not None
    conn_str = pg_conn_string(PG_URL)
    with psycopg.connect(conn_str) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
    required = {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
    assert required.issubset(tables), (
        f"Missing checkpointer tables: {required - tables}. "
        "checkpointer.setup() was not called in lifespan."
    )


def test_pg_conn_string_conversion() -> None:
    """pg_conn_string() strips the +asyncpg driver suffix for psycopg."""
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    assert pg_conn_string(url) == "postgresql://user:pass@host:5432/db"

    url2 = "postgresql+psycopg://user:pass@host:5432/db"
    assert pg_conn_string(url2) == "postgresql://user:pass@host:5432/db"


def test_detect_dialect() -> None:
    from dap_engine.persistence.db import detect_dialect

    assert detect_dialect("postgresql+asyncpg://...") == "postgresql"
    assert detect_dialect("postgresql://...") == "postgresql"
    assert detect_dialect("sqlite:///path/to/db") == "sqlite"
    assert detect_dialect("/bare/path.db") == "sqlite"
