"""Alembic environment for DAP engine (audit E6).

Both the CLI (``alembic upgrade head`` from ``apps/engine/``) and
the runtime (:func:`dap_engine.persistence.db._apply_alembic_migrations`)
invoke this module. The runtime path passes its own
``sqlalchemy.url`` via ``Config().set_main_option``; the CLI uses
the URL declared in ``alembic.ini``.

Online-mode only — we don't ship offline (--sql) support because
DAP doesn't have a "render SQL to a script and apply manually"
ops flow. Add it if a future deployment ever needs it.

Async DB drivers note: even though the engine uses ``asyncpg`` /
``aiosqlite`` at runtime for the API, migration application here
uses the SYNC driver (``psycopg`` for PostgreSQL, the SQLite
stdlib driver). Same convention as the existing legacy
``apply_migrations(engine)`` flow: migrations don't need the async
runtime, and using sync keeps the env.py code straightforward.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# The Base.metadata import drives ``--autogenerate``: alembic
# compares the current DB schema against this metadata and emits
# the diff as a new revision script. Keep this import accurate or
# autogenerate produces stale revisions.
from dap_engine.persistence.models import Base

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging. Skip when the runtime
# entry point invokes us without an .ini file (Config is built
# programmatically and has no ``config_file_name``).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (alembic --sql)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database.

    Both the CLI (``alembic upgrade head``) and the runtime
    integration use this path. The runtime integration injects an
    existing ``Engine`` via ``config.attributes["connection"]`` so we
    share the engine the rest of the app is using — avoids opening a
    second pool against the same DB. Falls back to building a fresh
    engine from ``sqlalchemy.url`` for the CLI path.
    """
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    # ``connectable`` may be either an Engine (CLI path) or a live
    # Connection (runtime path — the engine shares its connection
    # with us). Alembic's ``context.configure`` accepts both as
    # ``connection``; ``begin()`` on an Engine returns a context
    # manager that opens + closes, on a Connection it's a no-op
    # because the caller already manages the transaction lifetime.
    if hasattr(connectable, "connect"):
        with connectable.connect() as conn:
            _run(conn)
    else:
        _run(connectable)


def _run(connection: object) -> None:
    """Configure alembic with the live connection + run migrations.

    Helper so the two ``run_migrations_online`` branches (CLI vs.
    runtime-with-shared-connection) don't duplicate the configure
    call. ``connection`` is typed loose because alembic accepts
    either ``Engine`` or ``Connection`` here.
    """
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        # ``render_as_batch`` makes autogenerate emit SQLite-friendly
        # ALTER TABLE op blocks (SQLite doesn't support full ALTER
        # natively; batch mode does copy-to-temp-table behind the
        # scenes). Costs nothing on PostgreSQL.
        render_as_batch=connection.dialect.name == "sqlite"  # type: ignore[attr-defined]
        if connection is not None
        else False,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
