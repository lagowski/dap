"""Tests for ``runs.failure_reason`` (#260).

Pre-#260, ``mark_stale_running_runs_as_failed`` recorded the shutdown
reason via a synthetic ``StateSnapshotORM`` row with
``node_id="__shutdown__"`` and ``state["verification_reason"]``. That
sentinel polluted ``get_run_state_history`` consumers iterating real
node ids. The dedicated ``runs.failure_reason`` column replaces it.
"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.persistence import repository as repo
from dap_engine.persistence.db import create_engine_for_sqlite
from dap_engine.persistence.models import RunORM, StateSnapshotORM
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    with tempfile.TemporaryDirectory(prefix="dap-failure-reason-") as tmp:
        engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))
        yield sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _seed_running_run(session_factory: sessionmaker[Session]) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test",
        "branch": "main",
        "final_status": "running",
    }
    with session_factory() as session:
        session.add(
            RunORM(
                id=run_id,
                project_id=None,
                pipeline_id="pipe-x",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node=None,
                node_statuses={},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
            )
        )
        session.commit()
    return run_id


def test_mark_stale_writes_failure_reason_to_column(
    session_factory: sessionmaker[Session],
) -> None:
    """Stale-recovery should populate the new column directly — the row's
    ``failure_reason`` should match the supplied diagnostic string."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        count = repo.mark_stale_running_runs_as_failed(s, reason="engine restarted mid-run")
        s.commit()

    assert count == 1
    with session_factory() as s:
        run = s.get(RunORM, run_id)
        assert run is not None
        assert run.final_status == "failed"
        assert run.failure_reason == "engine restarted mid-run"


def test_mark_stale_does_not_create_synthetic_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    """The legacy ``__shutdown__`` snapshot pattern is gone (#260): no
    state-snapshot row is written by the stale-recovery sweep."""
    _seed_running_run(session_factory)

    with session_factory() as s:
        repo.mark_stale_running_runs_as_failed(s, reason="any reason")
        s.commit()

    with session_factory() as s:
        snapshots = s.scalars(
            select(StateSnapshotORM).where(StateSnapshotORM.node_id == "__shutdown__")
        ).all()
    assert snapshots == []


def test_get_run_exposes_failure_reason_via_pydantic(
    session_factory: sessionmaker[Session],
) -> None:
    """The Pydantic ``Run`` shape includes the new field; consumers (API,
    dashboard) read it from there rather than scraping snapshots."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.mark_stale_running_runs_as_failed(s, reason="boot recovery")
        s.commit()

    with session_factory() as s:
        # Admin-mode bypass — this is a direct-DB test that doesn't
        # exercise ownership; the lifecycle primitives are
        # user-agnostic by design.
        run = repo.get_run(s, run_id, actor_id=uuid.uuid4(), is_admin=True)

    assert run.final_status == "failed"
    assert run.failure_reason == "boot recovery"


def test_revive_clears_stale_failure_reason(
    session_factory: sessionmaker[Session],
) -> None:
    """Reviving a failed run via retry/skip transitions it back to ``running``.
    The previous-lifecycle ``failure_reason`` ("engine restarted mid-run",
    etc.) no longer describes the current state and must be cleared (#260) —
    otherwise a successfully-retried run would still surface the stale
    diagnostic to the dashboard."""
    run_id = _seed_running_run(session_factory)

    # Stale-recovery sweep marks it failed with a reason.
    with session_factory() as s:
        repo.mark_stale_running_runs_as_failed(s, reason="engine restarted mid-run")
        s.commit()

    # Operator hits retry → try_claim_revive flips back to running.
    with session_factory() as s:
        claimed = repo.try_claim_revive(s, run_id)
        s.commit()
    assert claimed is True

    with session_factory() as s:
        run = s.get(RunORM, run_id)
        assert run is not None
        assert run.final_status == "running"
        assert run.failure_reason is None


def test_clean_run_has_null_failure_reason(
    session_factory: sessionmaker[Session],
) -> None:
    """A run that finalises through normal paths (success / failed via the
    runner / aborted) must keep ``failure_reason=None`` — only the
    stale-recovery sweep writes to that column."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="success")
        s.commit()

    with session_factory() as s:
        # Admin-mode bypass — this is a direct-DB test that doesn't
        # exercise ownership; the lifecycle primitives are
        # user-agnostic by design.
        run = repo.get_run(s, run_id, actor_id=uuid.uuid4(), is_admin=True)

    assert run.final_status == "success"
    assert run.failure_reason is None


# ---------------------------------------------------------------------------
# Migration backfill — pre-#260 DB needs the ALTER on next startup
# ---------------------------------------------------------------------------


def _seed_pre_260_runs_table(db_path: Path) -> None:
    """Hand-roll a ``runs`` table without ``failure_reason`` to exercise
    the migration path on operator-deployed dev DBs."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                project_id TEXT,
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
        conn.commit()


def _column_names(db_path: Path, table: str) -> list[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def test_pre_260_db_gets_failure_reason_column() -> None:
    """An existing DB created before #260 must pick up the new column on
    next startup via migration 008."""
    with tempfile.TemporaryDirectory(prefix="dap-failure-reason-mig-") as tmp:
        db_path = Path(tmp) / "pre260.db"
        _seed_pre_260_runs_table(db_path)
        assert "failure_reason" not in _column_names(db_path, "runs")

        create_engine_for_sqlite(str(db_path))

        assert "failure_reason" in _column_names(db_path, "runs")
