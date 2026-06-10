"""Unit tests for dap_database URL/dialect helpers (#778 Phase 1).

Canonical home of the behaviour previously tested via
``dap_engine.persistence.db`` in ``tests/smoke/test_db_url_helpers.py`` —
the engine module now re-exports from ``dap_database``.
"""

from __future__ import annotations

from dap_database import (
    async_url_for,
    detect_dialect,
    normalize_pg_prefix,
    pg_conn_string,
    pg_sync_url,
    redact_database_url,
)


def test_detect_dialect_accepts_postgres_scheme() -> None:
    assert detect_dialect("postgres://user:pw@host/db") == "postgresql"
    assert detect_dialect("postgresql://x") == "postgresql"
    assert detect_dialect("postgresql+asyncpg://x") == "postgresql"
    assert detect_dialect("postgresql+psycopg://x") == "postgresql"
    assert detect_dialect("sqlite:///x") == "sqlite"
    assert detect_dialect("/bare/path.db") == "sqlite"


def test_normalize_pg_prefix() -> None:
    assert normalize_pg_prefix("postgres://user:pw@host/db") == "postgresql://user:pw@host/db"
    assert normalize_pg_prefix("postgresql://x") == "postgresql://x"
    assert normalize_pg_prefix("postgresql+asyncpg://x") == "postgresql+asyncpg://x"
    assert normalize_pg_prefix("sqlite:///x") == "sqlite:///x"


def test_pg_sync_url_forces_psycopg_driver() -> None:
    assert pg_sync_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert pg_sync_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert pg_sync_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert pg_sync_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_pg_conn_string_strips_driver_suffix() -> None:
    assert pg_conn_string("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert pg_conn_string("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    assert pg_conn_string("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"


def test_redact_database_url_masks_password() -> None:
    redacted = redact_database_url("postgresql+psycopg://user:s3cret@host:5432/db")
    assert "s3cret" not in redacted
    assert "user" in redacted


def test_redact_database_url_handles_password_with_at_sign() -> None:
    redacted = redact_database_url("postgresql://user:p%40ss@host/db")
    assert "p%40ss" not in redacted


def test_redact_database_url_falls_back_on_unparseable_input() -> None:
    assert redact_database_url("not a url at all") == "not a url at all"


def test_async_url_for_prefers_postgres_database_url() -> None:
    url = async_url_for("postgresql+asyncpg://u:p@h/db", "/tmp/state.db")
    assert url == "postgresql+psycopg://u:p@h/db"


def test_async_url_for_bare_postgres_scheme_gets_psycopg() -> None:
    url = async_url_for("postgresql://u:p@h/db", None)
    assert url == "postgresql+psycopg://u:p@h/db"


def test_async_url_for_sqlite_database_url_is_ignored_in_favour_of_db_path(
    tmp_path_factory: object,
) -> None:
    # A sqlite:// DATABASE_URL must not diverge the auth DB from the main
    # DB — only db_path is honoured for the sqlite branch.
    url = async_url_for("sqlite:///elsewhere.db", "/data/state.db")
    assert url.startswith("sqlite+aiosqlite:///")
    assert url.endswith("/data/state.db")


def test_async_url_for_raises_without_usable_input() -> None:
    import pytest

    with pytest.raises(ValueError, match="db_path"):
        async_url_for(None, None)
    with pytest.raises(ValueError, match="db_path"):
        async_url_for("sqlite:///x.db", None)
