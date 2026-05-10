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
      exist in SQLite — guard with ``_column_exists`` (which uses
      SQLAlchemy's ``Inspector``, not f-string PRAGMA) before the
      ALTER instead.
    - Forward-only. We don't track down-migrations.
    - Append to :data:`MIGRATIONS` in order. Names are stable string
      keys recorded in ``schema_migrations``; never rename a name once
      it ships, or operators will re-run the migration on next start.
    - Each migration runs in its own transaction with an atomic
      INSERT OR IGNORE claim on its name, so concurrent engine
      instances racing the same SQLite file don't double-apply.
      A failure rolls the transaction back (including the claim)
      and stops lifespan startup loudly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, inspect, text

logger = logging.getLogger("dap.engine.migrations")


@dataclass(frozen=True)
class Migration:
    """One named, idempotent schema change."""

    name: str
    apply: Callable[[Connection], None]


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    """Whether ``column`` exists on ``table`` according to the live schema.

    Uses SQLAlchemy's ``Inspector`` rather than f-string interpolating
    a ``PRAGMA table_info(<table>)`` — table/column names landing in
    SQL via string formatting are an identifier-injection footgun and
    don't survive reserved words / special chars. ``Inspector`` also
    abstracts the dialect so the helper can be reused if we ever
    target Postgres. Returns ``False`` when the table doesn't exist
    (rather than raising) so a migration can guard "ADD COLUMN" with
    a single check.
    """
    inspector = inspect(conn)
    if not inspector.has_table(table):
        return False
    return any(col["name"].lower() == column.lower() for col in inspector.get_columns(table))


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
    # project_id existence is validated by the API layer
    # (api/runs.py — POST /runs returns 422 when the project
    # doesn't exist or is archived) before the row ever reaches
    # the repository.
    conn.execute(text("ALTER TABLE runs ADD COLUMN project_id TEXT"))


def _002_node_execution_logs_add_extra_data(conn: Connection) -> None:
    """#145 — add ``node_execution_logs.extra_data`` for python-func audit payloads."""
    if _column_exists(conn, "node_execution_logs", "extra_data"):
        return
    conn.execute(text("ALTER TABLE node_execution_logs ADD COLUMN extra_data TEXT"))


def _003_pipeline_versions_add_ui_metadata(conn: Connection) -> None:
    """#226 — add ``pipeline_versions.ui_metadata`` for dashboard node positions.

    SQLite stores JSON as TEXT (the JSON ORM type is a thin wrapper around
    TEXT on SQLite).  PostgreSQL has a native JSON/JSONB type; use JSONB so
    the column type matches the ORM mapping and enables native JSON queries.
    """
    if _column_exists(conn, "pipeline_versions", "ui_metadata"):
        return
    dialect = conn.dialect.name
    col_type = "JSONB" if dialect == "postgresql" else "TEXT"
    conn.execute(text(f"ALTER TABLE pipeline_versions ADD COLUMN ui_metadata {col_type}"))


def _004_runs_index_started_at(conn: Connection) -> None:
    """#251 — index for ``list_runs`` ORDER BY ``started_at DESC``.

    The list endpoint sorts the runs table on every page and currently
    forces a full scan + sort. Both SQLite and PostgreSQL can walk the
    index backward, so an ASC index is sufficient and dialect-portable.
    """
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_runs_started_at ON runs (started_at)"))


def _005_runs_index_project_started(conn: Connection) -> None:
    """#251 — composite index for the project-scoped runs view.

    ``WHERE project_id = ? ORDER BY started_at DESC`` is the most
    common shape (project detail page). Leading with ``project_id``
    lets SQLite — which can't bitmap-intersect single-column indexes —
    satisfy filter and sort in one index walk.
    """
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_runs_project_started ON runs (project_id, started_at)")
    )


def _006_node_execution_logs_index_run_started(conn: Connection) -> None:
    """#251 — composite index for ``get_run`` log fan-out.

    ``GET /runs/{id}`` joins ``node_execution_logs`` on every call to
    populate per-node statuses (#233). High-frequency polling hammers
    this table; the composite supports both the ``WHERE run_id = ?``
    lookup and the ``ORDER BY started_at`` in one index.
    """
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_node_execution_logs_run_started "
            "ON node_execution_logs (run_id, started_at)"
        )
    )


def _007_runs_index_pipeline_started(conn: Connection) -> None:
    """#251 — composite index for the per-pipeline runs view.

    Mirror of ``_005_runs_index_project_started`` for the pipeline-
    scoped query (``WHERE pipeline_id = ? ORDER BY started_at DESC``)
    rendered by the pipeline detail page.
    """
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_runs_pipeline_started ON runs (pipeline_id, started_at)"
        )
    )


def _008_runs_add_failure_reason(conn: Connection) -> None:
    """#260 — add ``runs.failure_reason`` for stale-recovery diagnostics.

    Replaces the synthetic ``__shutdown__`` snapshot pattern with a
    dedicated nullable column. Existing dev DBs need the ALTER on
    next startup; fresh DBs already have the column from
    ``create_all``, so the guard keeps this a no-op there.
    """
    if _column_exists(conn, "runs", "failure_reason"):
        return
    conn.execute(text("ALTER TABLE runs ADD COLUMN failure_reason TEXT"))


def _009_create_users_table(conn: Connection) -> None:
    """v0.3 / #299 — create the ``users`` table for fastapi-users.

    Fresh DBs already have this table from ``Base.metadata.create_all``
    (the ORM mapping landed in this release alongside the migration).
    The migration backstops upgrades from pre-v0.3 dev DBs and records
    the schema version for paper trail.

    Columns mirror ``SQLAlchemyBaseUserTableUUID`` (id, email,
    hashed_password, is_active, is_superuser, is_verified) plus the
    extensions defined on ``UserORM`` (created_at, updated_at,
    deleted_at, last_login_at). The unique-email index is created
    explicitly because SQLite's CREATE TABLE … UNIQUE inline doesn't
    produce a named index that we can grep for in operations.
    """
    inspector = inspect(conn)
    if inspector.has_table("users"):
        return

    # Dialect-aware DDL: PostgreSQL gets a real UUID column and TIMESTAMPTZ
    # so the type matches the ``DateTime(timezone=True)`` ORM mapping; SQLite
    # uses CHAR(36) + TIMESTAMP because it lacks both native types and
    # treats them as TEXT internally either way.
    dialect = conn.dialect.name
    if dialect == "postgresql":
        id_type = "UUID"
        ts_type = "TIMESTAMPTZ"
    else:
        id_type = "CHAR(36)"
        ts_type = "TIMESTAMP"

    conn.execute(
        text(
            f"""
            CREATE TABLE users (
                id {id_type} NOT NULL PRIMARY KEY,
                email VARCHAR(320) NOT NULL,
                hashed_password VARCHAR(1024) NOT NULL,
                is_active BOOLEAN NOT NULL,
                is_superuser BOOLEAN NOT NULL,
                is_verified BOOLEAN NOT NULL,
                created_at {ts_type} NOT NULL,
                updated_at {ts_type} NOT NULL,
                deleted_at {ts_type} NULL,
                last_login_at {ts_type} NULL
            )
            """
        )
    )
    conn.execute(text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))


def _010_create_oauth_accounts_table(conn: Connection) -> None:
    """v0.3 / #299 — create the ``oauth_accounts`` table for fastapi-users.

    Mirrors the ORM mapping in ``OAuthAccountORM``. As with migration 9,
    fresh DBs already have the table from ``Base.metadata.create_all``;
    this migration backstops upgrades from pre-OAuth dev DBs.

    Indexes are created unconditionally with ``IF NOT EXISTS`` —
    ``create_all`` runs first but only creates indexes for *new* tables,
    so a DB that was upgraded incrementally could end up with the table
    but no indexes. The ``IF NOT EXISTS`` guards make the index creates
    a cheap no-op on fresh DBs that already have them from the ORM.
    """
    inspector = inspect(conn)
    if not inspector.has_table("oauth_accounts"):
        dialect = conn.dialect.name
        id_type = "UUID" if dialect == "postgresql" else "CHAR(36)"
        conn.execute(
            text(
                f"""
                CREATE TABLE oauth_accounts (
                    id {id_type} NOT NULL PRIMARY KEY,
                    user_id {id_type} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    oauth_name VARCHAR(100) NOT NULL,
                    access_token VARCHAR(1024) NOT NULL,
                    expires_at INTEGER NULL,
                    refresh_token VARCHAR(1024) NULL,
                    account_id VARCHAR(320) NOT NULL,
                    account_email VARCHAR(320) NOT NULL
                )
                """
            )
        )

    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_oauth_accounts_user_id ON oauth_accounts (user_id)")
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_oauth_accounts_provider_account "
            "ON oauth_accounts (oauth_name, account_id)"
        )
    )


MIGRATIONS: list[Migration] = [
    Migration(name="001_runs_add_project_id", apply=_001_runs_add_project_id),
    Migration(
        name="002_node_execution_logs_add_extra_data",
        apply=_002_node_execution_logs_add_extra_data,
    ),
    Migration(
        name="003_pipeline_versions_add_ui_metadata",
        apply=_003_pipeline_versions_add_ui_metadata,
    ),
    Migration(name="004_runs_index_started_at", apply=_004_runs_index_started_at),
    Migration(name="005_runs_index_project_started", apply=_005_runs_index_project_started),
    Migration(
        name="006_node_execution_logs_index_run_started",
        apply=_006_node_execution_logs_index_run_started,
    ),
    Migration(name="007_runs_index_pipeline_started", apply=_007_runs_index_pipeline_started),
    Migration(name="008_runs_add_failure_reason", apply=_008_runs_add_failure_reason),
    Migration(name="009_create_users_table", apply=_009_create_users_table),
    Migration(name="010_create_oauth_accounts_table", apply=_010_create_oauth_accounts_table),
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


def apply_migrations(engine: Engine) -> list[str]:
    """Run pending migrations in order. Returns the names of the ones applied.

    Each migration runs in its own transaction with an "INSERT OR
    IGNORE" claim on the migration name *before* the schema change.
    The claim is atomic: if two engine processes start against the
    same SQLite file, only one wins the row insert (rowcount == 1)
    and runs the migration body; the other sees rowcount == 0 and
    skips. If the migration body raises after the claim, the whole
    transaction rolls back — including the row insert — so the
    name doesn't get marked applied for a migration that didn't
    actually run.

    A failure is fatal: the engine refuses to soldier on against a
    half-migrated schema. The operator inspects the error, fixes
    the underlying problem, and restarts.
    """
    # PostgreSQL uses "ON CONFLICT DO NOTHING"; SQLite uses "INSERT OR IGNORE".
    # Both are semantically equivalent here — claim the migration name atomically
    # before running the body so concurrent processes don't double-apply.
    dialect = engine.dialect.name  # "sqlite" | "postgresql"
    if dialect == "postgresql":
        _claim_sql = text(
            "INSERT INTO schema_migrations (name, applied_at) "
            "VALUES (:name, :applied_at) ON CONFLICT (name) DO NOTHING"
        )
    else:
        _claim_sql = text(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"
        )

    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_table(conn)

    for migration in MIGRATIONS:
        with engine.begin() as conn:
            claim = conn.execute(
                _claim_sql,
                {
                    "name": migration.name,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
            if claim.rowcount == 0:
                # Already applied by us (previous startup) or by a
                # concurrent instance racing the same DB — either
                # way, nothing more to do.
                continue
            logger.info("applying schema migration: %s", migration.name)
            migration.apply(conn)
        applied.append(migration.name)

    if applied:
        logger.info("applied %d schema migration(s): %s", len(applied), applied)
    return applied
