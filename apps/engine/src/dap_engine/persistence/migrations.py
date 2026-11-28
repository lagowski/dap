"""In-code schema migrations (#109).

**Frozen as of v0.3.x (audit E6).** New schema changes land as
Alembic revisions under ``dap_engine/alembic/versions/`` — do NOT
extend ``MIGRATIONS[]``. This file stays so the legacy idempotent
upgrade path keeps working for pre-Alembic dev DBs.

Run **after** ``Base.metadata.create_all`` and **before**
``_apply_alembic_migrations`` in the engine lifespan (see
``persistence/db.py``).

Coexistence contract (audit E6):
    The engine's startup sequence is:

    1. ``Base.metadata.create_all(engine)`` — creates every table
       currently declared by the ORM. No-op for existing tables.
    2. ``apply_migrations(engine)`` — this module. Idempotent. On a
       fresh DB the ALTER bodies short-circuit via ``_column_exists``
       guards; on a pre-#109 dev DB they bring the schema forward to
       v0.3.x baseline.
    3. ``_apply_alembic_migrations(engine)`` — ``alembic upgrade
       head``. The baseline revision (``0001_baseline``) is a no-op
       marker; future revisions chain from there. On first Alembic
       run against an existing DB this just creates
       ``alembic_version`` and stamps the baseline.

    Net effect: pre-existing dev DBs keep upgrading through
    ``MIGRATIONS[]`` (no operator action required), then Alembic
    takes over for everything that lands after v0.3.x.

Why this exists at all:
    SQLAlchemy's ``create_all`` only creates *new* tables. It doesn't
    ALTER existing ones, so any column added in a later release is
    invisible on a pre-existing dev DB. We hit this with
    ``runs.project_id`` (#64); the ORM then crashed every list-runs
    call until the operator hand-applied the ALTER. The legacy
    migrations close that gap for everything that landed before
    Alembic was introduced.

Why this list is frozen:
    The original justification ("dev-grade, single-user SQLite") no
    longer holds — multi-user OAuth shipped in v0.3.0 and ops want
    versioned schema management. New schema changes use Alembic so
    that:
    - ``alembic revision --autogenerate`` writes migration boilerplate
      automatically from ORM diff.
    - ``alembic history`` / ``current`` give operators a clear
      "what's applied vs. pending" view.
    - Multi-deployment ops can apply migrations out-of-band
      (``cd apps/engine && alembic upgrade head``) without spinning
      up the engine process.

Historic contract for the 18 frozen migrations (no longer extended):
    - Pure SQL, idempotent. ``ALTER TABLE … IF NOT EXISTS`` doesn't
      exist in SQLite — guard with ``_column_exists`` (which uses
      SQLAlchemy's ``Inspector``, not f-string PRAGMA) before the
      ALTER instead.
    - Forward-only. We don't track down-migrations.
    - Stable string names recorded in ``schema_migrations``; never
      rename a name once it shipped, or operators will re-run the
      migration on next start.
    - Each migration runs in its own transaction with an atomic
      INSERT OR IGNORE claim on its name, so concurrent engine
      instances racing the same SQLite file don't double-apply.
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


def _012_add_user_id_to_resources(conn: Connection) -> None:
    """v0.3 / #299 (sub-A4a) — add nullable ``user_id`` to resource tables.

    Adds the column on ``agents``, ``pipelines``, ``projects``, ``runs``
    so the schema is ready for the ownership enforcement that lands in
    sub-A4b. The column is nullable here because:
      1. SQLite ``ALTER TABLE … ADD COLUMN`` can't add a NOT NULL
         column without a DEFAULT, and we don't have a system-user UUID
         to default to until #13 runs.
      2. The backfill (#13) populates *existing* rows; the
         application-layer contract that "every new row sets user_id"
         is enforced by the route handlers (sub-A4b), not the schema.

    PostgreSQL gets the same nullable shape so the two backends match.
    The follow-up sub-PR can ALTER COLUMN SET NOT NULL on PG once the
    contract is wired through; SQLite leaves it nullable forever
    because that dialect has no equivalent ALTER.

    Index creation runs **unconditionally** with ``IF NOT EXISTS``,
    separately from the ADD COLUMN guard. Otherwise on a fresh DB —
    where ``Base.metadata.create_all`` already created the column — the
    early-skip branch would also skip the indexes, leaving fresh
    installs without ``ix_*_user_id`` (the ORM only declares those
    indexes on three of the four tables; ``RunORM`` deliberately does
    not, so the index would be missing entirely otherwise).

    PostgreSQL upgrades additionally need an explicit FK constraint:
    ``ALTER TABLE ADD COLUMN`` (without REFERENCES) leaves the column
    integrity-checkless even though the ORM declares a ForeignKey.
    SQLite ignores trailing FK clauses, but to keep the migration
    dialect-correct we only emit the ALTER on PostgreSQL.
    """
    dialect = conn.dialect.name
    id_type = "UUID" if dialect == "postgresql" else "CHAR(36)"

    for table in ("agents", "pipelines", "projects", "runs"):
        if not _column_exists(conn, table, "user_id"):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id {id_type} NULL"))
            if dialect == "postgresql":
                # SQLite can't add an FK to an existing column; on PG we
                # tighten the constraint to match the ORM mapping
                # (ondelete=CASCADE). Constraint naming is explicit so
                # operators can drop / recreate it without guessing the
                # generated identifier.
                conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_user_id "
                        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
                    )
                )
        # Indexes always — IF NOT EXISTS makes this a cheap no-op when
        # create_all already produced them on a fresh DB, and a real
        # backstop for older dev DBs upgrading through this migration.
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)"))


def _013_backfill_user_id_via_system_user(conn: Connection) -> None:
    """v0.3 / #299 (sub-A4a) — backfill resource ownership.

    For each resource table with NULL ``user_id`` rows (i.e. data that
    pre-dates v0.3), set ``user_id`` to a synthetic ``system@local``
    user that's created on-the-fly if missing. The user is flagged
    ``is_superuser=True`` so admins listing resources see the legacy
    rows, plus ``is_active=False`` and a random unrecoverable password
    so **nobody can log in as system@local directly**. To re-assign
    ownership, an admin signs in with their own real account
    (after Phase B's dashboard auth flow ships) and reassigns the
    legacy resources via the admin panel (Phase C).

    Skipped entirely on a fresh DB where every resource table is
    empty — the system user only materialises when something actually
    needs claiming.
    """
    # Detect whether any backfill is needed.
    has_orphans = False
    for table in ("agents", "pipelines", "projects", "runs"):
        if not _column_exists(conn, table, "user_id"):
            continue
        result = conn.execute(text(f"SELECT 1 FROM {table} WHERE user_id IS NULL LIMIT 1"))
        if result.first() is not None:
            has_orphans = True
            break
    if not has_orphans:
        return

    # Find or create the system user. We can't use the ORM here (the
    # in-code migration runs against a raw SQLAlchemy Connection), so
    # SELECT-then-INSERT with the email as a uniqueness key.
    import secrets  # noqa: PLC0415 — local; migrations import lazily

    SYSTEM_EMAIL = "system@local"
    existing = conn.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": SYSTEM_EMAIL},
    ).first()
    if existing is None:
        # Hash a random throwaway password — fastapi-users uses pwdlib
        # / argon2 by default, but the migration has no easy access to
        # that. Storing a plain-random sha256 keeps the column non-NULL
        # without ever being a valid login (no UserManager will accept
        # it since the row is also is_active=False).
        import hashlib  # noqa: PLC0415
        import uuid as _uuid  # noqa: PLC0415

        system_id = str(_uuid.uuid4())
        random_secret = secrets.token_urlsafe(32)
        random_hash = hashlib.sha256(random_secret.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        conn.execute(
            text(
                """
                INSERT INTO users (
                    id, email, hashed_password,
                    is_active, is_superuser, is_verified,
                    created_at, updated_at, deleted_at, last_login_at
                ) VALUES (
                    :id, :email, :pw,
                    :is_active, :is_superuser, :is_verified,
                    :created_at, :updated_at, NULL, NULL
                )
                """
            ),
            {
                "id": system_id,
                "email": SYSTEM_EMAIL,
                "pw": random_hash,
                "is_active": False,
                "is_superuser": True,
                "is_verified": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        logger.warning(
            "Backfilled %s for legacy ownership claims on pre-v0.3 resources. "
            "Password is unrecoverable — sign in as an admin and re-assign "
            "ownership via /admin/users (Phase C) or hard-delete the row.",
            SYSTEM_EMAIL,
        )
    else:
        system_id = str(existing[0])

    for table in ("agents", "pipelines", "projects", "runs"):
        if not _column_exists(conn, table, "user_id"):
            continue
        conn.execute(
            text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": system_id},
        )


def _014_create_audit_log_table(conn: Connection) -> None:
    """v0.3 / #299 (sub-A4a) — create the ``audit_log`` table.

    Matches ``AuditLogORM``. Append-only; no FK to ``users`` so an audit
    row survives hard-deletion of the actor (audit integrity > referential
    cleanliness — see ORM docstring).

    Same idempotent IF-NOT-EXISTS index pattern as migrations 10 + 11.
    """
    inspector = inspect(conn)
    if not inspector.has_table("audit_log"):
        dialect = conn.dialect.name
        id_type = "UUID" if dialect == "postgresql" else "CHAR(36)"
        ts_type = "TIMESTAMPTZ" if dialect == "postgresql" else "TIMESTAMP"
        json_type = "JSONB" if dialect == "postgresql" else "TEXT"
        conn.execute(
            text(
                f"""
                CREATE TABLE audit_log (
                    id {id_type} NOT NULL PRIMARY KEY,
                    user_id {id_type} NULL,
                    event_type VARCHAR(100) NOT NULL,
                    event_data {json_type} NULL,
                    created_at {ts_type} NOT NULL
                )
                """
            )
        )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_audit_log_user_event ON audit_log (user_id, event_type)"
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log (created_at)")
    )


def _011_create_api_tokens_table(conn: Connection) -> None:
    """v0.3 / #299 — create the ``api_tokens`` table for CLI / script auth.

    Mirrors the ORM mapping in ``ApiTokenORM``. The token-prefix index
    is the hot path: every authenticated CLI call looks up by prefix
    first, then SHA-256-verifies the candidate hash. The user_id index
    supports the ``GET /auth/api-tokens`` listing endpoint (admin view
    or per-user).

    Same idempotency pattern as migration 10 — fresh DBs already have
    the table from ``create_all``; this migration backstops upgrades
    and ensures indexes exist either way.
    """
    inspector = inspect(conn)
    if not inspector.has_table("api_tokens"):
        dialect = conn.dialect.name
        id_type = "UUID" if dialect == "postgresql" else "CHAR(36)"
        ts_type = "TIMESTAMPTZ" if dialect == "postgresql" else "TIMESTAMP"
        conn.execute(
            text(
                f"""
                CREATE TABLE api_tokens (
                    id {id_type} NOT NULL PRIMARY KEY,
                    user_id {id_type} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    token_prefix VARCHAR(8) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    created_at {ts_type} NOT NULL,
                    expires_at {ts_type} NULL,
                    last_used_at {ts_type} NULL,
                    revoked_at {ts_type} NULL
                )
                """
            )
        )

    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_api_tokens_token_prefix ON api_tokens (token_prefix)")
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_api_tokens_user_id ON api_tokens (user_id)"))


def _015_promote_system_user_to_legacy_admin(conn: Connection) -> None:
    """v0.3 / #343 (sub-E1) — finish the pre-v0.3 upgrade story.

    Migration ``013`` created a synthetic ``system@local`` user as a
    *backfill anchor* — inactive, unrecoverable password, just enough
    to satisfy the application-layer ownership contract (the
    ``user_id`` column itself is nullable on the SQL side — SQLite
    can't ALTER COLUMN to NOT NULL — but the route handlers refuse
    to insert NULL going forward; see migration 012). That left
    operators upgrading from 0.0.1 with no way to actually log in to
    the multi-user instance — every resource was owned by an
    unreachable account.

    This migration finishes the job: promote ``system@local`` to
    ``legacy-admin@local`` with a freshly-generated, *usable*
    Argon2id-hashed password and set the row active. Resource
    ownership stays linked through the same user id — no second
    UPDATE pass on the resource tables.

    Output split (Copilot review of sub-E1):
      - ``print()`` writes the password to **stdout** — captured once
        by an interactive operator.
      - ``logger.warning()`` writes a follow-up note *without* the
        password to the engine's normal log channel — long-lived log
        aggregation never contains the secret, but oncall still gets
        a heads-up to look at stdout.

    Idempotent: if the row was already promoted (e.g. re-running
    migrations), or if ``legacy-admin@local`` already exists from a
    fresh ``dap init``, this migration skips.

    Fresh installs (no orphan resources → no ``system@local`` was ever
    created by 013) also skip — they get their admin from
    ``dap init`` instead.
    """
    import secrets  # noqa: PLC0415 — lazy, like 013

    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    legacy_email = "legacy-admin@local"
    system_email = "system@local"

    # If a legacy admin already exists (re-run, or someone ran
    # ``dap init --admin-email=legacy-admin@local``), bow out.
    legacy = conn.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": legacy_email},
    ).first()
    if legacy is not None:
        return

    # No system user → either fresh install, or someone already
    # promoted the row. Either way, nothing to do.
    system_user = conn.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": system_email},
    ).first()
    if system_user is None:
        return

    # Generate a usable password. 22 base64url chars = ~132 bits of
    # entropy, same as ``dap init`` uses for its generated path.
    password = secrets.token_urlsafe(22)
    hashed = PasswordHelper().hash(password)
    now = datetime.now(UTC).isoformat()

    # ``is_superuser = TRUE`` belt-and-suspenders: 013 already set it
    # when it created the row, but an operator could in principle have
    # toggled it off (e.g. via a manual SQL fix-up) between then and
    # now. Re-asserting here guarantees the row promotion lands as an
    # actual admin no matter what intermediate state preceded it.
    conn.execute(
        text(
            """
            UPDATE users
            SET email = :legacy_email,
                hashed_password = :hashed,
                is_active = :is_active,
                is_superuser = :is_superuser,
                is_verified = :is_verified,
                deleted_at = NULL,
                updated_at = :updated_at
            WHERE email = :system_email
            """
        ),
        {
            "legacy_email": legacy_email,
            "hashed": hashed,
            "is_active": True,
            "is_superuser": True,
            "is_verified": True,
            "updated_at": now,
            "system_email": system_email,
        },
    )

    # Password to stdout (operator's terminal); separate WARN log
    # without the password (long-term log aggregation never sees the
    # secret). Both reference the same email so it's clear they
    # describe the same event.
    print(
        f"Migrated single-user install. Bootstrap admin: {legacy_email} "
        f"with password={password}. Change immediately at /admin/users.",
        flush=True,
    )
    logger.warning(
        "Promoted system@local → %s during pre-v0.3 upgrade. "
        "The generated password was printed to stdout exactly once — "
        "capture it now and rotate at /admin/users.",
        legacy_email,
    )


def _018_create_instance_env_vars_table(conn: Connection) -> None:
    """#388 — instance-level env vars (shared across projects, merged at runtime).

    Fresh DBs already have this from ``Base.metadata.create_all``; this
    migration only fires on pre-#388 dev DBs. The column types mirror
    the ORM declarations (``InstanceEnvVarORM``) so SQLite and Postgres
    agree. The unique index on ``key`` lets upserts target the row in
    one query (``SELECT ... WHERE key = ?``) instead of scanning.
    """
    inspector = inspect(conn)
    if inspector.has_table("instance_env_vars"):
        return

    is_pg = conn.dialect.name == "postgresql"
    id_type = "UUID" if is_pg else "VARCHAR(36)"
    ts_type = "TIMESTAMP WITH TIME ZONE" if is_pg else "TIMESTAMP"

    conn.execute(
        text(
            f"""
            CREATE TABLE instance_env_vars (
                id {id_type} PRIMARY KEY,
                key VARCHAR(255) NOT NULL,
                ciphertext TEXT NOT NULL,
                preview VARCHAR(8) NOT NULL,
                created_at {ts_type} NOT NULL,
                updated_at {ts_type} NOT NULL,
                CONSTRAINT uq_instance_env_vars_key UNIQUE (key)
            )
            """
        )
    )


def _017_runs_add_gate_payload(conn: Connection) -> None:
    """#364 — store gate context (task_assignments, spec) on the Run row.

    Allows the dashboard to display task assignments when a run is paused at
    an approval gate, without querying the LangGraph checkpoint store.
    Fresh DBs already have the column from ``create_all``.
    """
    if _column_exists(conn, "runs", "gate_payload"):
        return
    col_type = "JSONB" if conn.dialect.name == "postgresql" else "TEXT"
    conn.execute(text(f"ALTER TABLE runs ADD COLUMN gate_payload {col_type}"))


def _016_runs_add_paused_at_node(conn: Connection) -> None:
    """#363 — store which gate node caused the run to pause.

    Allows the dashboard to show a targeted "Approve" action instead of
    the generic "Resume" button when a run is paused at an approval gate.
    Fresh DBs already have the column from ``create_all``; this guards
    upgrades from pre-#363 dev DBs.
    """
    if _column_exists(conn, "runs", "paused_at_node"):
        return
    conn.execute(text("ALTER TABLE runs ADD COLUMN paused_at_node TEXT"))


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
    Migration(name="011_create_api_tokens_table", apply=_011_create_api_tokens_table),
    Migration(name="012_add_user_id_to_resources", apply=_012_add_user_id_to_resources),
    Migration(
        name="013_backfill_user_id_via_system_user",
        apply=_013_backfill_user_id_via_system_user,
    ),
    Migration(name="014_create_audit_log_table", apply=_014_create_audit_log_table),
    Migration(
        name="015_promote_system_user_to_legacy_admin",
        apply=_015_promote_system_user_to_legacy_admin,
    ),
    Migration(name="016_runs_add_paused_at_node", apply=_016_runs_add_paused_at_node),
    Migration(name="017_runs_add_gate_payload", apply=_017_runs_add_gate_payload),
    Migration(
        name="018_create_instance_env_vars_table",
        apply=_018_create_instance_env_vars_table,
    ),
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
