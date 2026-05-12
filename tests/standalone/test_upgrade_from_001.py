"""End-to-end smoke for the v0.0.1 → v0.3.0 upgrade path (#343 sub-E1).

What we're verifying: an operator upgrading a pre-v0.3 ``.dap/state.db``
to v0.3.0 ends up with a usable admin account whose password printed
to stdout during the migration, AND every pre-existing resource is
still visible (now owned by that admin).

Rather than ship a binary fixture, we build the pre-v0.3 DB
programmatically: run migrations 1–8 only (the pre-v0.3 chain), seed
a handful of resource rows with NULL ``user_id``, then call
``apply_migrations`` for the full chain and assert against the
post-state. This keeps the test self-documenting and immune to
future migration churn — schema changes that break the fixture will
break it loudly here, not silently in a binary blob.
"""

from __future__ import annotations

import io
import sqlite3
import uuid
from contextlib import redirect_stderr
from pathlib import Path

from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.migrations import MIGRATIONS, apply_migrations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

# Names of the migrations that landed *before* the v0.3 chain (001–008).
# Running just these against a fresh DB simulates the pre-v0.3 schema.
PRE_V03_MIGRATION_NAMES = frozenset(
    {
        "001_runs_add_project_id",
        "002_node_execution_logs_add_extra_data",
        "003_pipeline_versions_add_ui_metadata",
        "004_runs_index_started_at",
        "005_runs_index_project_started",
        "006_node_execution_logs_index_run_started",
        "007_runs_index_pipeline_started",
        "008_runs_add_failure_reason",
    }
)


def _create_pre_v03_database(db_path: Path) -> None:
    """Build a SQLite file in the v0.0.1 schema state with sample rows.

    Approach:
      1. ``create_all`` to lay down the modern schema.
      2. Insert resource rows via SQLAlchemy ORM so every Python-level
         default fires (``current_version``, ``trigger_source``, ...).
         Then NULL out ``user_id`` with a raw UPDATE — that's the only
         column we explicitly want pre-v0.3-shaped.
      3. Mark migrations 001–008 as already applied so
         ``apply_migrations`` doesn't re-run them, then leave 009+
         pending. This makes the test exercise just the v0.3 portion
         of the chain.
    """
    from datetime import datetime as _dt

    # Lazy imports keep this module importable when the engine package
    # isn't on the path during plugin discovery.
    from dap_engine.persistence.models import (
        AgentORM,
        Base,
        PipelineORM,
        ProjectORM,
        RunORM,
    )
    from sqlalchemy.orm import Session

    # Raw SQLAlchemy engine — NOT ``create_engine_for_sqlite`` because
    # that helper auto-runs ``apply_migrations`` for app-startup
    # convenience, which would race ahead of what we're trying to test.
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    now = _dt(2025, 6, 1, 12, 0, 0)
    with Session(engine) as session:
        project = ProjectORM(
            id=str(uuid.uuid4()),
            name="legacy-project",
            created_at=now,
            updated_at=now,
        )
        pipeline = PipelineORM(
            id=str(uuid.uuid4()),
            name="legacy-pipeline",
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        agent = AgentORM(
            id=str(uuid.uuid4()),
            name="legacy-agent",
            role="writer",
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        # RunORM has many NOT NULL columns w/o defaults — use minimal
        # but valid sentinel values for trigger_source/initial_state/
        # final_status etc.
        run = RunORM(
            id=str(uuid.uuid4()),
            pipeline_id=pipeline.id,
            pipeline_version=1,
            trigger_source="legacy-import",
            initial_state={},
            final_status="succeeded",
            started_at=now,
        )
        session.add_all([project, pipeline, agent, run])
        session.commit()

        # Clear user_id on every seeded row — pre-v0.3 had no such
        # column, so this is the post-create_all equivalent of that
        # absence.
        for table in ("projects", "pipelines", "agents", "runs"):
            session.execute(text(f"UPDATE {table} SET user_id = NULL"))
        session.commit()

    # Mark the pre-v0.3 migrations as already applied so the upgrade
    # under test only runs 009+.
    with sqlite3.connect(db_path) as cx:
        cx.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for name in PRE_V03_MIGRATION_NAMES:
            cx.execute(
                "INSERT OR IGNORE INTO schema_migrations (name, applied_at) "
                "VALUES (?, '2025-01-01T00:00:00Z')",
                (name,),
            )
        cx.commit()

    engine.dispose()


def _try_login(db_path: Path, email: str, password: str) -> bool:
    """Spin up the engine app and POST to ``/auth/jwt/login``.

    Returns True on a 2xx; False otherwise. Same helper as the
    ``dap init`` smoke; duplicated rather than imported because
    bootstrapping cross-test-module imports adds more friction than
    the ~10 lines this saves.
    """
    cfg = EngineConfig(
        db_path=str(db_path),
        auth_jwt_secret="test-secret-for-upgrade-smoke",
    )
    app = create_app(cfg)
    with TestClient(app) as client:
        response = client.post(
            "/auth/jwt/login",
            data={"username": email, "password": password},
        )
    return 200 <= response.status_code < 300


def _extract_password(stderr_text: str) -> str | None:
    """Pull the generated password out of the migration's stderr line.

    The migration prints exactly:
        Migrated single-user install. Bootstrap admin: legacy-admin@local
        with password=<token>. Change immediately at /admin/users.

    We grep on the unambiguous ``password=`` prefix.
    """
    for line in stderr_text.splitlines():
        marker = "password="
        idx = line.find(marker)
        if idx == -1:
            continue
        rest = line[idx + len(marker) :]
        # Strip the trailing ``. Change immediately ...`` chatter.
        return rest.split(".", 1)[0].strip()
    return None


# --------------------------------------------------------------------- #
# 1. Happy path: pre-v0.3 install upgrades to a working admin login
# --------------------------------------------------------------------- #


def test_upgrade_creates_legacy_admin_with_working_password(tmp_path: Path) -> None:
    """A pre-v0.3 SQLite with orphan resources upgrades to v0.3 with a
    ``legacy-admin@local`` admin whose printed password authenticates."""
    db_path = tmp_path / "state.db"
    _create_pre_v03_database(db_path)

    # Run the rest of the chain (009..015). Capture stderr — the new
    # migration prints the generated password there.
    buf = io.StringIO()
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with redirect_stderr(buf):
        applied = apply_migrations(engine)
    engine.dispose()

    # The pre-v0.3 chain (001–008) was pre-marked as applied, so only
    # 009–015 should land this run.
    assert "015_promote_system_user_to_legacy_admin" in applied, f"expected 015 in {applied}"

    password = _extract_password(buf.getvalue())
    assert password is not None and len(password) > 8, (
        f"didn't find generated password in stderr:\n{buf.getvalue()}"
    )

    assert _try_login(db_path, "legacy-admin@local", password), (
        "legacy-admin login failed with the generated password"
    )


# --------------------------------------------------------------------- #
# 2. Ownership backfill: every pre-existing resource is now claimed
# --------------------------------------------------------------------- #


def test_upgrade_backfills_resource_ownership(tmp_path: Path) -> None:
    """After upgrade, every pre-v0.3 row must have ``user_id`` set to
    the legacy-admin's id — no NULL owners remain."""
    db_path = tmp_path / "state.db"
    _create_pre_v03_database(db_path)

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with redirect_stderr(io.StringIO()):
        apply_migrations(engine)
    engine.dispose()

    with sqlite3.connect(db_path) as cx:
        admin_id = cx.execute("SELECT id FROM users WHERE email = 'legacy-admin@local'").fetchone()
        assert admin_id is not None, "legacy-admin@local row missing"
        admin_uuid = admin_id[0]

        # Exactly one user — no leftover ``system@local`` after the
        # promotion (the row was renamed in-place).
        user_count = cx.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert user_count == 1, f"expected exactly 1 user, got {user_count}"

        for table in ("agents", "pipelines", "projects", "runs"):
            null_owners = cx.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
            ).fetchone()[0]
            assert null_owners == 0, f"{table} still has NULL user_id rows"

            mismatched = cx.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id != ?",
                (admin_uuid,),
            ).fetchone()[0]
            assert mismatched == 0, f"{table} has rows owned by someone other than legacy-admin"


# --------------------------------------------------------------------- #
# 3. Idempotency: re-running migrations doesn't double-bootstrap
# --------------------------------------------------------------------- #


def test_upgrade_is_idempotent_on_rerun(tmp_path: Path) -> None:
    """Running ``apply_migrations`` a second time against an
    already-upgraded DB must not regenerate the password, change the
    email, or duplicate the user. Operators restart the engine all
    the time — the migration ledger guards re-application."""
    db_path = tmp_path / "state.db"
    _create_pre_v03_database(db_path)

    # First run — upgrades.
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    buf1 = io.StringIO()
    with redirect_stderr(buf1):
        apply_migrations(engine)
    engine.dispose()
    first_password = _extract_password(buf1.getvalue())
    assert first_password is not None

    # Second run — should be a no-op.
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    buf2 = io.StringIO()
    with redirect_stderr(buf2):
        applied = apply_migrations(engine)
    engine.dispose()

    assert applied == [], f"second run shouldn't apply anything, got {applied}"
    assert _extract_password(buf2.getvalue()) is None, (
        "second run printed a password — migration ran twice"
    )

    # The first password still works.
    assert _try_login(db_path, "legacy-admin@local", first_password)


# --------------------------------------------------------------------- #
# 4. Fresh install: no ``system@local`` was created → migration skips
# --------------------------------------------------------------------- #


def test_upgrade_skips_on_fresh_install(tmp_path: Path) -> None:
    """On a brand-new DB (no orphan resources), migration 013 doesn't
    create ``system@local`` and migration 015 has nothing to promote.
    Operators bootstrap their admin via ``dap init`` instead."""
    db_path = tmp_path / "state.db"

    # Match the engine app lifespan: create_all FIRST so the tables
    # exist, then apply_migrations runs the chain end-to-end.
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    from dap_engine.persistence.models import Base

    Base.metadata.create_all(engine)

    buf = io.StringIO()
    with redirect_stderr(buf):
        applied = apply_migrations(engine)
    engine.dispose()

    assert "015_promote_system_user_to_legacy_admin" in applied, (
        "migration should still be marked applied (idempotent no-op)"
    )
    assert _extract_password(buf.getvalue()) is None, (
        "fresh install shouldn't print a password — no legacy admin"
    )

    with sqlite3.connect(db_path) as cx:
        users = cx.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert users == 0, f"fresh install should have zero users, got {users}"


# Suppress the unused-import warning — ``MIGRATIONS`` is referenced
# in the test file only to anchor PRE_V03_MIGRATION_NAMES against the
# actual module-level list during code review.
_ = MIGRATIONS
