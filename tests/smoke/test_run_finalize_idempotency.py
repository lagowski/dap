"""Regression tests for ``finalize_run`` / ``pause_run`` idempotency (#257).

The cancel-during-runner-error race surfaced in the audit (B2): the
inner ``except RunnerError`` finalises a run as ``failed``, then the
outer ``except CancelledError`` re-finalises as ``aborted`` because
the task got cancelled while the inner write was still committing.
The fix makes both ``finalize_run`` and ``pause_run`` idempotent — the
first writer wins, subsequent calls are no-ops.

These tests poke the persistence layer directly so the guard isn't
hidden behind the engine / runner / async timing.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.persistence import repository as repo
from dap_engine.persistence.db import create_engine_for_sqlite
from dap_engine.persistence.models import RunORM
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    with tempfile.TemporaryDirectory(prefix="dap-finalize-idem-") as tmp:
        engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))
        yield sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _seed_running_run(session_factory: sessionmaker[Session]) -> str:
    """Insert a run in ``running`` state and return its id."""
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


def _final_status(session_factory: sessionmaker[Session], run_id: str) -> str:
    with session_factory() as session:
        run = session.get(RunORM, run_id)
        assert run is not None
        return run.final_status


def _ended_at(session_factory: sessionmaker[Session], run_id: str) -> datetime | None:
    with session_factory() as session:
        run = session.get(RunORM, run_id)
        assert run is not None
        return run.ended_at


def test_finalize_run_first_writer_wins(session_factory: sessionmaker[Session]) -> None:
    """The cancel-during-runner-error race: inner finalise as ``failed`` survives
    even when the outer ``CancelledError`` handler then tries to finalise as
    ``aborted``."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="failed")
        s.commit()

    failed_at = _ended_at(session_factory, run_id)
    assert _final_status(session_factory, run_id) == "failed"
    assert failed_at is not None

    # Simulate the outer CancelledError handler firing late.
    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="aborted")
        s.commit()

    assert _final_status(session_factory, run_id) == "failed"
    # ended_at must not advance — that would erase the original
    # finalisation timestamp clients rely on.
    assert _ended_at(session_factory, run_id) == failed_at


def test_finalize_run_does_not_overwrite_success(session_factory: sessionmaker[Session]) -> None:
    """Same guard applies to a happy-path finalise: a stray late finaliser
    can't downgrade ``success`` to ``aborted`` / ``failed``."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="success")
        s.commit()

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="failed")
        s.commit()

    assert _final_status(session_factory, run_id) == "success"


def test_pause_run_skips_terminal_runs(session_factory: sessionmaker[Session]) -> None:
    """``pause_run`` racing with the inner runner error must not overwrite
    a terminal status — it would also reset ``ended_at`` to ``None`` and lose
    the recorded outcome."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="failed")
        s.commit()

    failed_at = _ended_at(session_factory, run_id)

    with session_factory() as s:
        repo.pause_run(s, run_id)
        s.commit()

    assert _final_status(session_factory, run_id) == "failed"
    assert _ended_at(session_factory, run_id) == failed_at


def test_finalize_run_first_call_normal_path(session_factory: sessionmaker[Session]) -> None:
    """The idempotency guard must not break the normal first-finalise path
    on a still-running row."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.finalize_run(s, run_id, final_status="success")
        s.commit()

    assert _final_status(session_factory, run_id) == "success"
    assert _ended_at(session_factory, run_id) is not None


def test_pause_run_first_call_normal_path(session_factory: sessionmaker[Session]) -> None:
    """``pause_run`` on a running run still flips status to ``paused``."""
    run_id = _seed_running_run(session_factory)

    with session_factory() as s:
        repo.pause_run(s, run_id)
        s.commit()

    assert _final_status(session_factory, run_id) == "paused"
    assert _ended_at(session_factory, run_id) is None
