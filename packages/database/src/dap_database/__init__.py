"""dap-database — shared SQLAlchemy plumbing for DAP (#778 Phase 1).

URL/dialect helpers, raw engine factories (SQLite WAL / PostgreSQL), and
sync+async session factories. No ORM models, no migrations — schema
ownership stays with the consumer (``dap_engine.persistence``).
"""

from dap_database.engines import (
    create_async_engine_for_url,
    create_postgresql_engine,
    create_sqlite_engine,
)
from dap_database.sessions import (
    make_async_session_factory,
    make_session_factory,
    session_scope,
)
from dap_database.urls import (
    async_url_for,
    detect_dialect,
    normalize_pg_prefix,
    pg_conn_string,
    pg_sync_url,
    redact_database_url,
)

__all__ = [
    "async_url_for",
    "create_async_engine_for_url",
    "create_postgresql_engine",
    "create_sqlite_engine",
    "detect_dialect",
    "make_async_session_factory",
    "make_session_factory",
    "normalize_pg_prefix",
    "pg_conn_string",
    "pg_sync_url",
    "redact_database_url",
    "session_scope",
]
