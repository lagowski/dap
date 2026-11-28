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

The dialect-agnostic plumbing (URL helpers, raw engine factories, session
factories) lives in the shared ``dap-database`` package (#778 Phase 1);
this module adds the engine-specific part: schema creation + migrations.
The pre-#778 public names are re-exported so existing imports keep working.
"""

from __future__ import annotations

# Re-exported for backwards compatibility — consumers (auth/db.py,
# api/settings.py, CLI bootstrap, tests) import these from here. The
# underscore aliases preserve the pre-#778 private names.
from dap_database import (
    create_postgresql_engine,
    create_sqlite_engine,
    detect_dialect,
    make_session_factory,
    normalize_pg_prefix,
    pg_conn_string,
    pg_sync_url,
    redact_database_url,
    session_scope,
)
from sqlalchemy import Engine

from dap_engine.persistence.migrations import apply_migrations
from dap_engine.persistence.models import Base

_normalize_pg_prefix = normalize_pg_prefix
_pg_sync_url = pg_sync_url

__all__ = [
    "create_engine_for_postgresql",
    "create_engine_for_sqlite",
    "detect_dialect",
    "make_session_factory",
    "pg_conn_string",
    "redact_database_url",
    "session_scope",
]

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


def _init_schema(engine: Engine) -> None:
    """Bring a freshly-created engine's database up to the current schema.

    Order matters: ``create_all`` creates any missing tables (no-op
    for existing ones, *including ones that lack columns the current
    model defines*). Then ``apply_migrations`` runs the in-code
    ALTERs (#109) that catch up older schemas to today's shape. New
    DBs see all current columns from ``create_all`` and the
    migrations are recorded as applied no-ops. Finally,
    ``_apply_alembic_migrations`` brings the DB up to head against
    the Alembic-managed revision chain (audit E6) — its baseline
    is a no-op marker so this is effectively just a stamp on first
    run.
    """
    Base.metadata.create_all(engine)
    apply_migrations(engine)
    _apply_alembic_migrations(engine)


def create_engine_for_sqlite(db_path: str) -> Engine:
    """SQLite engine (WAL pragmas via ``dap_database``) with schema applied."""
    engine = create_sqlite_engine(db_path)
    _init_schema(engine)
    return engine


def create_engine_for_postgresql(database_url: str) -> Engine:
    """PostgreSQL engine (psycopg v3 via ``dap_database``) with schema applied.

    Accepts the canonical DAP ``postgresql+asyncpg://`` URL form; the shared
    factory rewrites it to ``postgresql+psycopg://`` for the sync engine.
    """
    engine = create_postgresql_engine(database_url)
    _init_schema(engine)
    return engine
