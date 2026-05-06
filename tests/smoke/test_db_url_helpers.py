"""Pure unit tests for DB URL parsing helpers — no Postgres required (#188).

Lives outside test_postgresql_backend.py because that module is skipped
when DAP_DATABASE_URL is unset, which is the default in CI; these helpers
must be exercised every run.
"""

from __future__ import annotations

from dap_engine.persistence.db import (
    _normalize_pg_prefix,
    _pg_sync_url,
    detect_dialect,
    pg_conn_string,
)


def test_detect_dialect_accepts_postgres_scheme() -> None:
    """Heroku-style ``postgres://`` must route to postgresql, not silently to sqlite."""
    assert detect_dialect("postgres://user:pw@host/db") == "postgresql"
    assert detect_dialect("postgresql://x") == "postgresql"
    assert detect_dialect("postgresql+asyncpg://x") == "postgresql"
    assert detect_dialect("sqlite:///x") == "sqlite"
    assert detect_dialect("/bare/path.db") == "sqlite"


def test_normalize_pg_prefix() -> None:
    assert _normalize_pg_prefix("postgres://user:pw@host/db") == "postgresql://user:pw@host/db"
    assert _normalize_pg_prefix("postgresql://x") == "postgresql://x"
    assert _normalize_pg_prefix("postgresql+asyncpg://x") == "postgresql+asyncpg://x"
    assert _normalize_pg_prefix("postgresql+psycopg://x") == "postgresql+psycopg://x"
    assert _normalize_pg_prefix("sqlite:///x") == "sqlite:///x"


def test_pg_conn_string_handles_postgres_scheme() -> None:
    """End-to-end: ``postgres://`` survives the conn-string rewrite as ``postgresql://``."""
    assert pg_conn_string("postgres://user:pw@host/db") == "postgresql://user:pw@host/db"
    assert (
        pg_conn_string("postgresql+asyncpg://user:pw@host/db")
        == "postgresql://user:pw@host/db"
    )
    assert (
        pg_conn_string("postgresql+psycopg://user:pw@host/db")
        == "postgresql://user:pw@host/db"
    )


def test_pg_sync_url_forces_psycopg_driver() -> None:
    """_pg_sync_url always selects psycopg v3 — bare postgresql:// would default to psycopg2."""
    assert _pg_sync_url("postgresql+asyncpg://x") == "postgresql+psycopg://x"
    assert _pg_sync_url("postgresql://x") == "postgresql+psycopg://x"
    assert _pg_sync_url("postgres://x") == "postgresql+psycopg://x"
    # Already-psycopg URL passes through untouched
    assert _pg_sync_url("postgresql+psycopg://x") == "postgresql+psycopg://x"
    # End-to-end with a realistic URL
    assert (
        _pg_sync_url("postgres://user:pw@host:5432/db")
        == "postgresql+psycopg://user:pw@host:5432/db"
    )
