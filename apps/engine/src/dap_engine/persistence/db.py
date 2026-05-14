"""SQLAlchemy engine factory — SQLite (WAL mode) and PostgreSQL.

Dialect selection is driven by the ``DAP_DATABASE_URL`` env var (see
``__main__.py``).  The URL prefix determines which path is taken:

* ``sqlite://`` (or bare file path via DAP_DB_PATH) → SQLite + WAL pragmas.
  Default for local dev; no extra dependencies needed.
* ``postgresql+asyncpg://`` → PostgreSQL via psycopg (sync engine).  Requires
  ``psycopg[binary]`` and ``langgraph-checkpoint-postgres`` (declared as
  deps in pyproject.toml). The ``+asyncpg`` driver suffix is accepted for
  legacy compatibility but rewritten to ``+psycopg`` before SQLAlchemy use;
  the asyncpg package is not actually imported.

The rest of the engine (ORM models, repository, migrations) is dialect-agnostic
and works unchanged against both backends.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.persistence.migrations import apply_migrations
from dap_engine.persistence.models import Base

# ---------------------------------------------------------------------------
# Dialect helpers
# ---------------------------------------------------------------------------


def detect_dialect(url: str) -> Literal["sqlite", "postgresql"]:
    """Return ``"postgresql"`` for postgres-family URLs, else ``"sqlite"``.

    Accepts ``postgresql+asyncpg://``, ``postgresql+psycopg://``, bare
    ``postgresql://``, and heroku-style ``postgres://`` — all routed to the
    PostgreSQL branch.
    """
    return "postgresql" if url.startswith(("postgresql", "postgres://")) else "sqlite"


def _normalize_pg_prefix(url: str) -> str:
    """Rewrite heroku-style ``postgres://`` to ``postgresql://`` for SQLAlchemy.

    SQLAlchemy 1.4+ rejects the bare ``postgres://`` scheme; normalize at
    consumption time so callers can pass either form.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _pg_sync_url(database_url: str) -> str:
    """Return a SQLAlchemy URL guaranteed to use psycopg v3 as the sync driver.

    Normalizes ``postgres://`` to ``postgresql://``, rewrites ``+asyncpg`` to
    ``+psycopg``, and forces ``+psycopg`` on bare ``postgresql://`` URLs.
    The bare scheme is critical: SQLAlchemy defaults bare ``postgresql://``
    to psycopg2, which is not a project dependency, so create_engine would
    fail at runtime without this rewrite.
    """
    url = _normalize_pg_prefix(database_url)
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def pg_conn_string(database_url: str) -> str:
    """Return a bare ``postgresql://`` conn string for psycopg (e.g. AsyncPostgresSaver)."""
    url = _normalize_pg_prefix(database_url)
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def redact_database_url(url: str) -> str:
    """Mask the password in a database URL for log/UI display.

    Uses SQLAlchemy's ``make_url`` + ``render_as_string(hide_password=True)``
    so URL-encoded passwords, IPv6 hosts, query params, and other quirks of
    real-world database URLs round-trip safely. A naive string-splitting
    redaction would leak credentials on URLs whose password contains ``@``
    or other edge characters (Copilot review on PR #332 / #419).

    Falls back to the input string if SQLAlchemy can't parse the URL — we
    prefer surfacing a raw value over silently stripping something
    important. Callers that log this should also fail loudly enough that
    a malformed URL becomes obvious quickly.
    """
    try:
        return make_url(url).render_as_string(hide_password=True)
    except ArgumentError:
        return url


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA foreign_keys = ON")
    # Allow up to 5 s of retry before raising "database is locked". Needed
    # when the node executor opens a short-lived log session concurrently
    # with the long-lived background session (#162).
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.close()


def create_engine_for_sqlite(db_path: str) -> Engine:
    absolute = Path(db_path).resolve()
    absolute.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{absolute}",
        echo=False,
        future=True,
        # Allow up to 10 s of busy-wait when another connection holds a write
        # lock. Needed when node_executor opens a short-lived log session
        # concurrently with the long-lived background session (#162).
        connect_args={"timeout": 10},
    )
    event.listen(engine, "connect", _enable_sqlite_pragmas)

    # Order matters: ``create_all`` creates any missing tables (no-op
    # for existing ones, *including ones that lack columns the current
    # model defines*). Then ``apply_migrations`` runs the in-code
    # ALTERs (#109) that catch up older schemas to today's shape. New
    # DBs see all current columns from ``create_all`` and the
    # migrations are recorded as applied no-ops.
    Base.metadata.create_all(engine)
    apply_migrations(engine)

    return engine


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def create_engine_for_postgresql(database_url: str) -> Engine:
    """Create a synchronous SQLAlchemy engine for PostgreSQL.

    Uses ``psycopg`` (v3) as the sync DBAPI driver.  The caller supplies a URL
    with the ``postgresql+asyncpg://`` scheme (used as the canonical DAP URL);
    this function rewrites it to ``postgresql+psycopg://`` for the sync engine.

    Pool settings for a single long-running process:
    - ``pool_size=5`` — modest default.
    - ``max_overflow=10`` — burst headroom.
    - ``pool_pre_ping=True`` — validate connections after idle periods
      (k8s NodePort is behind NAT that drops idle TCP).
    """
    sync_url = _pg_sync_url(database_url)

    engine = create_engine(
        sync_url,
        echo=False,
        future=True,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,  # recycle before NAT (k8s NodePort) drops idle TCP
    )

    Base.metadata.create_all(engine)
    apply_migrations(engine)

    return engine


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
