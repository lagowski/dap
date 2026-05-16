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
# Alembic runtime integration (audit E6)
# ---------------------------------------------------------------------------
#
# Hand-off contract between the legacy ``apply_migrations`` (in-code
# MIGRATIONS[] list, idempotent, frozen as of v0.3.x) and Alembic:
#
# 1. ``Base.metadata.create_all`` creates every currently-declared
#    table on a fresh DB.
# 2. ``apply_migrations`` records all 18 legacy migrations as
#    applied (idempotent on a fresh DB; runs missing ALTERs on a
#    pre-Alembic dev DB).
# 3. ``_apply_alembic_migrations`` (below) creates ``alembic_version``
#    if missing, stamps ``0001_baseline`` if first run, and applies
#    any newer revisions.
#
# New schema changes after v0.3.x are alembic-only — never extend
# ``MIGRATIONS[]``. See ``src/dap_engine/alembic/__init__.py`` for the
# CLI flow + revision-creation recipe.


def _apply_alembic_migrations(engine: Engine) -> None:
    """Run ``alembic upgrade head`` against ``engine`` using the
    in-package migration scripts.

    Programmatic invocation (no sidecar alembic.ini at runtime) so
    the wheel works without a co-deployed config file. Configuration
    happens in code:

    - ``script_location`` points at the ``alembic`` subpackage that
      ships inside the wheel (``dap_engine.alembic``).
    - The existing ``Engine`` is shared with Alembic via
      ``config.attributes["connection"]`` — env.py picks it up so
      we don't open a second connection pool against the same DB.

    Idempotent: ``alembic upgrade head`` is a no-op when the DB is
    already at head. On first run against a DB that doesn't have an
    ``alembic_version`` table yet, alembic creates the table and
    applies revisions from the beginning. Our ``0001_baseline`` is
    a no-op marker, so this fresh-table case applies zero SQL.

    Errors propagate to the caller (engine startup), matching the
    legacy ``apply_migrations`` policy: refuse to start against a
    half-migrated schema.
    """
    # Local imports — alembic is heavy and only needed at startup,
    # not on every import of ``persistence.db``. Same convention as
    # the per-provider lazy imports in the runtimes registry.
    from importlib.resources import files  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    # Resolve the migration package's filesystem path. ``files()``
    # returns a path-like that works whether the wheel is installed
    # editable or as a zip — required for the runtime case where
    # the engine is shipped as a wheel without source files alongside.
    alembic_root = files("dap_engine") / "alembic"

    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_root))
    # Share the live engine instead of opening a new connection pool.
    # ``env.py`` reads this from ``config.attributes`` rather than the
    # ``sqlalchemy.url`` main option (which we leave unset at runtime).
    cfg.attributes["connection"] = engine

    command.upgrade(cfg, "head")


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
    # migrations are recorded as applied no-ops. Finally,
    # ``_apply_alembic_migrations`` brings the DB up to head against
    # the Alembic-managed revision chain (audit E6) — its baseline
    # is a no-op marker so this is effectively just a stamp on first
    # run.
    Base.metadata.create_all(engine)
    apply_migrations(engine)
    _apply_alembic_migrations(engine)

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
    _apply_alembic_migrations(engine)

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
