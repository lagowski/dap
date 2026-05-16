"""Smoke test — sub-A4a schema migrations (#299).

Verifies:
- Migrations 12/13/14 ran on engine startup (paper trail in
  ``schema_migrations`` table).
- ``audit_log`` table exists with the expected columns + indexes.
- The ``user_id`` column exists (nullable) on every resource table.
- Backfill (#13) creates a ``system@local`` user and stamps existing
  NULL-user_id rows with that user's id.

Backfill verification works by simulating a pre-v0.3 DB: we insert a
resource row with ``user_id = NULL`` directly via SQL, then re-trigger
migration #13 by re-running ``apply_migrations`` against the same
engine. The migration is idempotent (it only acts on NULL rows), so
this is the canonical "old DB upgraded to v0.3" path.
"""

from __future__ import annotations

from dap_engine.persistence.migrations import apply_migrations
from fastapi.testclient import TestClient
from sqlalchemy import text


def test_user_id_columns_exist_on_resource_tables(client: TestClient) -> None:
    """Every resource table must expose ``user_id`` after migration #12."""
    engine = client.app.state.db_engine  # type: ignore[attr-defined]
    for table in ("agents", "pipelines", "projects", "runs"):
        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        assert "user_id" in cols, f"{table} missing user_id column"


def test_audit_log_table_exists(client: TestClient) -> None:
    """``audit_log`` table must be created by migration #14."""
    engine = client.app.state.db_engine  # type: ignore[attr-defined]
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(audit_log)"))}
    assert {"id", "user_id", "event_type", "event_data", "created_at"} <= cols


def test_migrations_recorded(client: TestClient) -> None:
    """``schema_migrations`` carries a paper-trail entry for each new migration."""
    engine = client.app.state.db_engine  # type: ignore[attr-defined]
    with engine.connect() as conn:
        applied = {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}
    for name in (
        "012_add_user_id_to_resources",
        "013_backfill_user_id_via_system_user",
        "014_create_audit_log_table",
    ):
        assert name in applied, f"{name} not recorded"


def test_backfill_claims_orphan_rows(client: TestClient) -> None:
    """A pre-v0.3-shaped row (user_id NULL) is backfilled to system@local.

    Inserts a synthetic agent with NULL user_id directly via SQL, then
    re-runs ``apply_migrations`` to trigger #13 (the backfill skipped
    on the first run because the table was empty). After re-running,
    the row's user_id must point at the freshly-created system user.
    """
    engine = client.app.state.db_engine  # type: ignore[attr-defined]
    now_iso = "2026-05-10T12:00:00+00:00"

    with engine.begin() as conn:
        # Drop the prior schema_migrations entry for #13 so it re-runs.
        conn.execute(
            text("DELETE FROM schema_migrations WHERE name = :n"),
            {"n": "013_backfill_user_id_via_system_user"},
        )
        # Insert a legacy-shaped agent.
        conn.execute(
            text(
                """
                INSERT INTO agents (id, name, role, current_version, created_at, updated_at)
                VALUES ('legacy-agent', 'Legacy', 'task_selector', 1, :t, :t)
                """
            ),
            {"t": now_iso},
        )

    apply_migrations(engine)

    with engine.connect() as conn:
        # System user must exist.
        sys_row = conn.execute(
            text("SELECT id, is_superuser, is_active FROM users WHERE email = 'system@local'")
        ).first()
        assert sys_row is not None, "system@local user not created by backfill"
        sys_id, is_superuser, is_active = sys_row
        assert bool(is_superuser) is True
        # Inactive: no one can log in as the system user without an admin password reset.
        assert bool(is_active) is False

        # The orphan agent now points at the system user.
        agent_user = conn.execute(
            text("SELECT user_id FROM agents WHERE id = 'legacy-agent'")
        ).scalar_one()
        assert str(agent_user) == str(sys_id)
