"""Raw SQLAlchemy engine factories — SQLite (WAL mode) and PostgreSQL.

Extracted from ``dap_engine.persistence.db`` / ``dap_engine.auth.db``
(#778 Phase 1). These factories create **no schema**: ``create_all`` and
migrations stay with the consumer, which owns the ORM models. That keeps
this package importable by the CLI (and tests) without dragging in the
engine's model registry or Alembic.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dap_database.urls import async_url_for, pg_sync_url

__all__ = [
    "create_async_engine_for_url",
    "create_postgresql_engine",
    "create_sqlite_engine",
]

# Allow up to 5 s of retry before raising "database is locked". Needed
# when a short-lived log session runs concurrently with the long-lived
# background session (#162).
SQLITE_BUSY_TIMEOUT_MS = 5000

# Connection-open busy wait — same contention scenario as above (#162).
SQLITE_CONNECT_TIMEOUT_S = 10


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_sqlite_engine(db_path: str) -> Engine:
    """Synchronous SQLite engine with WAL pragmas; parent dirs are created."""
    absolute = Path(db_path).resolve()
    absolute.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{absolute}",
        echo=False,
        future=True,
        connect_args={"timeout": SQLITE_CONNECT_TIMEOUT_S},
    )
    event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def create_postgresql_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_recycle_s: int = 1800,
) -> Engine:
    """Synchronous PostgreSQL engine using psycopg (v3) as the DBAPI driver.

    Accepts any postgres-family URL (``postgresql+asyncpg://`` is the
    canonical DAP form) and rewrites it to ``postgresql+psycopg://``.

    Pool defaults for a single long-running process:
    - ``pool_size=5`` — modest default.
    - ``max_overflow=10`` — burst headroom.
    - ``pool_pre_ping=True`` — validate connections after idle periods
      (k8s NodePort is behind NAT that drops idle TCP).
    - ``pool_recycle_s=1800`` — recycle before NAT drops idle TCP.
    """
    return create_engine(
        pg_sync_url(database_url),
        echo=False,
        future=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=pool_recycle_s,
    )


def create_async_engine_for_url(
    database_url: str | None,
    db_path: str | None,
    *,
    pool_size: int = 2,
    max_overflow: int = 4,
) -> AsyncEngine:
    """Async engine over the same database the sync engine uses.

    Pool sizes default smaller than the sync engine's — the async side
    (fastapi-users auth) is sparse traffic compared to the runs/state
    endpoints. ``pool_pre_ping`` is on so a token-validation request
    after a long idle period doesn't fail on a NAT-dropped TCP
    connection.

    Note: SQLite's aiosqlite dialect uses ``NullPool``-ish queueing where
    the pool kwargs are still accepted; PostgreSQL honours them fully.
    """
    return create_async_engine(
        async_url_for(database_url, db_path),
        echo=False,
        future=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )
