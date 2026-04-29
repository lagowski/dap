"""In-code schema migrations (#109).

Run **after** ``Base.metadata.create_all`` in the engine lifespan.

Why this exists:
    SQLAlchemy's ``create_all`` only creates *new* tables. It doesn't
    ALTER existing ones, so any column added in a later release is
    invisible on a pre-existing dev DB. We hit this with
    ``runs.project_id`` (#64); the ORM then crashed every list-runs
    call until the operator hand-applied the ALTER.

Why not Alembic (yet):
    At v0.x scale (single-user, SQLite, dev-grade) the few schema
    changes per release fit comfortably in a hand-written list. The
    intent is to switch to Alembic once schema stabilises and we get
    a second deployment that needs migrations applied independently
    — see #109 for the rationale.

Contract for new migrations:
    - Pure SQL, idempotent. ``ALTER TABLE … IF NOT EXISTS`` doesn't
      exist in SQLite — guard with ``_column_exists`` / ``PRAGMA
      table_info`` before the ALTER instead.
    - Forward-only. We don't track down-migrations.
    - Append to :data:`MIGRATIONS` in order. Names are stable string
      keys recorded in ``schema_migrations``; never rename a name once
      it ships, or operators will re-run the migration on next start.
    - Each migration runs in its own transaction. A failure stops
      the lifespan startup (loud) — the operator can inspect the
      error and either patch up DB state manually or roll the
      release back.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, text

logger = logging.getLogger("dap.engine.migrations")


@dataclass(frozen=True)
class Migration:
    """One named, idempotent schema change."""

    name: str
    apply: Callable[[Connection], None]


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    """``PRAGMA table_info`` returns rows: (cid, name, type, notnull, dflt, pk).

    We match by ``name`` — case-insensitive (SQLite is). Returns ``False``
    if the table itself doesn't exist (the row set is empty).
    """
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1].lower() == column.lower() for row in rows)


# ---------------------------------------------------------------------------
# Migration list — append-only. Don't rename existing names.
# ---------------------------------------------------------------------------


def _001_runs_add_project_id(conn: Connection) -> None:
    """Backfill #64 — add ``runs.project_id`` on pre-v0.6 dev DBs.

    The column was introduced in #64 but the engine relied on
    ``create_all`` to apply it, which silently no-ops on an existing
    table. Operators saw ``OperationalError: no such column:
    runs.project_id`` until they ran a manual ALTER. This migration
    makes that automatic on next startup.

    Fresh DBs already have the column from ``create_all`` — guard
    keeps this migration a no-op there. The recorded ``applied_at``
    in ``schema_migrations`` is still useful as a paper trail.
    """
    if _column_exists(conn, "runs", "project_id"):
        return
    # SQLite ALTER TABLE … ADD COLUMN can't include REFERENCES on
    # an existing table — the FK constraint would only apply if
    # declared at table creation. We accept that for the backfill;
    # the application validates project_id existence at insert time
    # in repository.create_run anyway.
    conn.execute(text("ALTER TABLE runs ADD COLUMN project_id TEXT"))


MIGRATIONS: list[Migration] = [
    Migration(name="001_runs_add_project_id", apply=_001_runs_add_project_id),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


_CREATE_TABLE_SQL = text(
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        name TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
)


def _ensure_table(conn: Connection) -> None:
    conn.execute(_CREATE_TABLE_SQL)


def _applied_names(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT name FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def apply_migrations(engine: Engine) -> list[str]:
    """Run pending migrations in order. Returns the names of the ones applied.

    Each migration runs in its own transaction. Already-applied
    migrations (by name) are skipped. The list is consulted from the
    in-memory ``MIGRATIONS`` constant — adding a new entry there is
    the only way to introduce a new migration.

    Raises whatever the migration raises — a failure is fatal, the
    operator needs to see it loudly during startup rather than have
    the engine soldier on against a half-migrated schema.
    """
    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_table(conn)
        already = _applied_names(conn)

    for migration in MIGRATIONS:
        if migration.name in already:
            continue
        with engine.begin() as conn:
            logger.info("applying schema migration: %s", migration.name)
            migration.apply(conn)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)",
                ),
                {
                    "name": migration.name,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
        applied.append(migration.name)

    if applied:
        logger.info("applied %d schema migration(s): %s", len(applied), applied)
    return applied
