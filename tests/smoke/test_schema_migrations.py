"""Tests for ``dap_engine.persistence.migrations`` (#109).

Covers:

- Fresh DB: ``apply_migrations`` records every migration but no
  schema diff is needed (``create_all`` already laid down today's
  shape).
- Pre-migration DB: a hand-rolled ``runs`` table without
  ``project_id`` gets the column added on engine startup, the
  GET /runs endpoint returns 200 instead of crashing.
- Idempotence: running the migrations twice doesn't re-apply.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.db import create_engine_for_sqlite
from dap_engine.persistence.migrations import MIGRATIONS, apply_migrations
from sqlalchemy import text


@pytest.fixture
def tmpdir_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="dap-migrations-test-") as tmp:
        yield Path(tmp)


def _column_names(db_path: Path, table: str) -> list[str]:
    """Inspect a SQLite file directly without going through SQLAlchemy."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Fresh DB — every migration recorded, no actual diff to apply
# ---------------------------------------------------------------------------


def test_fresh_db_records_all_migrations(tmpdir_path: Path) -> None:
    db_path = tmpdir_path / "fresh.db"
    engine = create_engine_for_sqlite(str(db_path))

    with engine.begin() as conn:
        applied = conn.execute(
            text("SELECT name FROM schema_migrations ORDER BY name"),
        ).fetchall()

    expected = {m.name for m in MIGRATIONS}
    assert {row[0] for row in applied} == expected


def test_apply_migrations_is_idempotent(tmpdir_path: Path) -> None:
    """Calling ``apply_migrations`` a second time after fresh init applies
    nothing — names already in ``schema_migrations`` are skipped."""
    db_path = tmpdir_path / "idem.db"
    engine = create_engine_for_sqlite(str(db_path))

    second_run = apply_migrations(engine)
    assert second_run == []


# ---------------------------------------------------------------------------
# Pre-migration DB — runs table exists without project_id (#64 backfill)
# ---------------------------------------------------------------------------


def _seed_pre_064_runs_table(db_path: Path) -> None:
    """Hand-roll the ``runs`` schema as it looked before #64 — no
    ``project_id`` column. Column names match today's model exactly
    (``initial_state``, ``node_statuses`` — JSON typed) so SQLAlchemy
    can hydrate the row after migration; the only difference from
    today's create_all output is the missing column. ``create_all``
    will no-op on this existing table, which is exactly the bug the
    migration fixes."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                pipeline_version INTEGER NOT NULL,
                trigger_source TEXT NOT NULL,
                initial_state JSON NOT NULL,
                current_node TEXT,
                node_statuses JSON NOT NULL,
                final_status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0
            )
            """,
        )
        # PipelineState requires run_id / repo / branch — seed a
        # complete-ish state matching what a real pre-#64 row would
        # have looked like, otherwise SQLAlchemy → Pydantic hydration
        # fails on read with ValidationError.
        initial_state = (
            '{"run_id": "run-pre-064", "repo": "test/repo", '
            '"branch": "main", "final_status": "success"}'
        )
        conn.execute(
            "INSERT INTO runs (id, pipeline_id, pipeline_version, "
            "trigger_source, initial_state, node_statuses, "
            "final_status, started_at, tokens_used, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run-pre-064",
                "pipe-1",
                1,
                "cli",
                initial_state,
                "{}",
                "success",
                "2026-04-01T00:00:00+00:00",
                0,
                0.0,
            ),
        )
        conn.commit()


def test_pre_064_db_gets_project_id_column(tmpdir_path: Path) -> None:
    db_path = tmpdir_path / "pre-064.db"
    _seed_pre_064_runs_table(db_path)
    assert "project_id" not in _column_names(db_path, "runs")

    create_engine_for_sqlite(str(db_path))

    columns = _column_names(db_path, "runs")
    assert "project_id" in columns


def test_pre_064_db_existing_row_survives_with_null_project(
    tmpdir_path: Path,
) -> None:
    db_path = tmpdir_path / "pre-064-row.db"
    _seed_pre_064_runs_table(db_path)

    engine = create_engine_for_sqlite(str(db_path))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, project_id FROM runs WHERE id = :id"),
            {"id": "run-pre-064"},
        ).first()

    assert row is not None
    assert row[0] == "run-pre-064"
    assert row[1] is None


def test_pre_064_db_get_runs_endpoint_returns_200(tmpdir_path: Path) -> None:
    """End-to-end: a pre-#64 DB used to crash GET /runs with
    ``OperationalError: no such column: runs.project_id``. After the
    migration, the endpoint should serve the (now ``project_id=null``)
    row without error.

    Promote the fixture user to admin so the listing surfaces the
    seeded ``user_id=NULL`` legacy row — the post-#299 ownership rule
    only treats those rows as visible to admins.
    """
    from dap_engine.persistence.models import UserORM

    from tests.smoke._auth import authed_test_client

    db_path = tmpdir_path / "pre-064-endpoint.db"
    _seed_pre_064_runs_table(db_path)

    config = EngineConfig(
        db_path=str(db_path),
        auth_jwt_secret="pre-064-test-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as client:
        with app.state.session_factory() as session:
            user_orm = session.query(UserORM).filter(UserORM.email == "test@local.dev").one()
            user_orm.is_superuser = True
            session.commit()
        login = client.post(
            "/auth/jwt/login",
            data={"username": "test@local.dev", "password": "test-password-123"},
        )
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        response = client.get("/runs")

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert "run-pre-064" in ids


def test_pre_064_db_records_migration_as_applied(tmpdir_path: Path) -> None:
    db_path = tmpdir_path / "pre-064-record.db"
    _seed_pre_064_runs_table(db_path)
    engine = create_engine_for_sqlite(str(db_path))

    with engine.begin() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}
    assert "001_runs_add_project_id" in names
