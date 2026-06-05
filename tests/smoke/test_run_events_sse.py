"""Tests for the SSE run-events endpoint (#662, Phase 3a).

Two layers:

1. Unit tests for the pure ``_diff_events`` helper and the ``RunSnapshot``
   builder — the bulk of the coverage. These exercise the event-derivation
   logic in isolation, no DB / no event loop.
2. A small set of integration tests that connect to
   ``GET /runs/{id}/events`` against a seeded *terminal* run (so the stream
   closes promptly and the test can never hang), asserting the wire format,
   the ``snapshot`` and ``run_finished`` events, the content-type, and the
   auth/404 behaviour.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.api.run_events import (
    POLL_INTERVAL,
    _diff_events,
    _snapshot_from_run,
    _sse,
)
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import NodeExecutionLogORM, RunORM, UserORM
from dap_types import NodeExecutionLog, Run
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.smoke._auth import authed_test_client, register_and_login

# ---------------------------------------------------------------------------
# Helpers for building Run / NodeExecutionLog fixtures for the unit tests
# ---------------------------------------------------------------------------


def _make_run(
    *,
    run_id: str = "run-1",
    final_status: str = "running",
    current_node: str | None = None,
    node_statuses: dict[str, str] | None = None,
    ended_at: datetime | None = None,
) -> Run:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
        "available_issues": [],
        "selected_issue_ids": [],
        "tests_generated": False,
        "test_files": [],
        "test_generation_errors": [],
        "max_attempts": 3,
        "attempt": 0,
        "tests_passed": False,
        "last_test_output": "",
        "modified_files": [],
        "implementation_notes": None,
        "verification_status": "pending",
        "verification_reason": None,
        "final_status": final_status,
        "extensions": {},
    }
    return Run(
        id=run_id,
        pipeline_id="pipe-1",
        pipeline_version=1,
        trigger_source="cli",
        initial_state=initial_state,  # type: ignore[arg-type]
        current_node=current_node,
        node_statuses=node_statuses or {},  # type: ignore[arg-type]
        final_status=final_status,  # type: ignore[arg-type]
        started_at=now,
        ended_at=ended_at,
        created_at=now,
        updated_at=now,
    )


def _make_node_log(
    *,
    node_id: str,
    status: str = "success",
    duration_ms: int = 850,
    tokens_used: int = 120,
    cost_usd: float = 0.001,
    ended_at: datetime | None = None,
) -> NodeExecutionLog:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    return NodeExecutionLog(
        id=str(uuid.uuid4()),
        run_id="run-1",
        node_id=node_id,
        agent_id="agent-1",
        runtime_id="api-call",
        started_at=now,
        ended_at=ended_at if ended_at is not None else now,
        prompt_xml="<agent_prompt/>",
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        status=status,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# _sse wire-format helper
# ---------------------------------------------------------------------------


def test_sse_wire_format() -> None:
    line = _sse("snapshot", {"run_id": "abc", "final_status": "running"})
    assert line == 'event: snapshot\ndata: {"run_id": "abc", "final_status": "running"}\n\n'
    # The data line must be valid JSON.
    body = line.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(body) == {"run_id": "abc", "final_status": "running"}


# ---------------------------------------------------------------------------
# _snapshot_from_run builder
# ---------------------------------------------------------------------------


def test_snapshot_from_run_captures_state() -> None:
    run = _make_run(
        final_status="running",
        current_node="implement",
        node_statuses={"select": "success", "implement": "running"},
    )
    logs = [_make_node_log(node_id="select", status="success")]
    snap = _snapshot_from_run(run, logs)
    assert snap.run_id == "run-1"
    assert snap.final_status == "running"
    assert snap.current_node == "implement"
    assert snap.node_statuses == {"select": "success", "implement": "running"}
    # node metrics keyed by node_id for finished-node payloads
    assert snap.node_logs["select"].status == "success"


# ---------------------------------------------------------------------------
# _diff_events — initial snapshot (prev is None)
# ---------------------------------------------------------------------------


def test_diff_initial_snapshot_emits_snapshot_event() -> None:
    cur = _snapshot_from_run(
        _make_run(
            final_status="running",
            current_node="select",
            node_statuses={"select": "running"},
        ),
        [],
    )
    events = _diff_events(None, cur)
    assert len(events) == 1
    event, data = events[0]
    assert event == "snapshot"
    assert data["run_id"] == "run-1"
    assert data["final_status"] == "running"
    assert data["current_node"] == "select"
    assert data["node_statuses"] == {"select": "running"}


def test_diff_initial_snapshot_of_terminal_run_emits_snapshot_then_finished() -> None:
    ended = datetime(2026, 6, 5, 12, 5, tzinfo=UTC)
    cur = _snapshot_from_run(
        _make_run(
            final_status="success",
            node_statuses={"select": "success"},
            ended_at=ended,
        ),
        [_make_node_log(node_id="select", status="success")],
    )
    events = _diff_events(None, cur)
    names = [e for e, _ in events]
    assert names == ["snapshot", "run_finished"]
    finished = events[-1][1]
    assert finished["run_id"] == "run-1"
    assert finished["final_status"] == "success"
    assert finished["ended_at"] == ended.isoformat()


# ---------------------------------------------------------------------------
# _diff_events — no change
# ---------------------------------------------------------------------------


def test_diff_no_change_emits_nothing() -> None:
    snap = _snapshot_from_run(
        _make_run(
            final_status="running",
            current_node="select",
            node_statuses={"select": "running"},
        ),
        [],
    )
    # Build an identical second snapshot.
    same = _snapshot_from_run(
        _make_run(
            final_status="running",
            current_node="select",
            node_statuses={"select": "running"},
        ),
        [],
    )
    assert _diff_events(snap, same) == []


# ---------------------------------------------------------------------------
# _diff_events — run_status changes
# ---------------------------------------------------------------------------


def test_diff_run_status_on_current_node_change() -> None:
    prev = _snapshot_from_run(
        _make_run(current_node="select", node_statuses={"select": "running"}), []
    )
    cur = _snapshot_from_run(
        _make_run(
            current_node="implement",
            node_statuses={"select": "success", "implement": "running"},
        ),
        [],
    )
    events = _diff_events(prev, cur)
    names = [e for e, _ in events]
    assert "run_status" in names
    run_status = next(d for e, d in events if e == "run_status")
    assert run_status["current_node"] == "implement"
    assert run_status["final_status"] == "running"


def test_diff_run_status_on_final_status_change_is_not_terminal_for_paused() -> None:
    prev = _snapshot_from_run(_make_run(final_status="running"), [])
    cur = _snapshot_from_run(_make_run(final_status="paused"), [])
    events = _diff_events(prev, cur)
    names = [e for e, _ in events]
    assert "run_status" in names
    # paused is NOT terminal — no run_finished
    assert "run_finished" not in names


# ---------------------------------------------------------------------------
# _diff_events — node_started
# ---------------------------------------------------------------------------


def test_diff_node_started_when_node_first_runs() -> None:
    prev = _snapshot_from_run(_make_run(node_statuses={"select": "success"}), [])
    cur = _snapshot_from_run(
        _make_run(node_statuses={"select": "success", "implement": "running"}), []
    )
    events = _diff_events(prev, cur)
    started = [d for e, d in events if e == "node_started"]
    assert len(started) == 1
    assert started[0]["node_id"] == "implement"


def test_diff_node_started_from_pending_to_running() -> None:
    prev = _snapshot_from_run(_make_run(node_statuses={"implement": "pending"}), [])
    cur = _snapshot_from_run(_make_run(node_statuses={"implement": "running"}), [])
    events = _diff_events(prev, cur)
    started = [d for e, d in events if e == "node_started"]
    assert len(started) == 1
    assert started[0]["node_id"] == "implement"


# ---------------------------------------------------------------------------
# _diff_events — node_finished (with metrics)
# ---------------------------------------------------------------------------


def test_diff_node_finished_includes_metrics() -> None:
    ended = datetime(2026, 6, 5, 12, 3, tzinfo=UTC)
    prev = _snapshot_from_run(_make_run(node_statuses={"select": "running"}), [])
    cur = _snapshot_from_run(
        _make_run(node_statuses={"select": "success"}),
        [
            _make_node_log(
                node_id="select",
                status="success",
                duration_ms=910,
                tokens_used=200,
                cost_usd=0.005,
                ended_at=ended,
            )
        ],
    )
    events = _diff_events(prev, cur)
    finished = [d for e, d in events if e == "node_finished"]
    assert len(finished) == 1
    payload = finished[0]
    assert payload["node_id"] == "select"
    assert payload["status"] == "success"
    assert payload["duration_ms"] == 910
    assert payload["tokens_used"] == 200
    assert payload["cost_usd"] == 0.005
    assert payload["ended_at"] == ended.isoformat()


def test_diff_node_finished_for_failed_and_skipped() -> None:
    for terminal_node_status in ("failed", "skipped"):
        prev = _snapshot_from_run(_make_run(node_statuses={"n": "running"}), [])
        cur = _snapshot_from_run(
            _make_run(node_statuses={"n": terminal_node_status}),
            [_make_node_log(node_id="n", status=terminal_node_status)],
        )
        events = _diff_events(prev, cur)
        finished = [d for e, d in events if e == "node_finished"]
        assert len(finished) == 1, terminal_node_status
        assert finished[0]["status"] == terminal_node_status


def test_diff_node_finished_without_log_falls_back_gracefully() -> None:
    # Status transitioned but no node log row yet — emit node_finished with
    # the status from node_statuses and metrics omitted/zeroed.
    prev = _snapshot_from_run(_make_run(node_statuses={"n": "running"}), [])
    cur = _snapshot_from_run(_make_run(node_statuses={"n": "success"}), [])
    events = _diff_events(prev, cur)
    finished = [d for e, d in events if e == "node_finished"]
    assert len(finished) == 1
    assert finished[0]["node_id"] == "n"
    assert finished[0]["status"] == "success"


# ---------------------------------------------------------------------------
# _diff_events — terminal run emits run_finished
# ---------------------------------------------------------------------------


def test_diff_terminal_transition_emits_run_finished_last() -> None:
    ended = datetime(2026, 6, 5, 12, 9, tzinfo=UTC)
    prev = _snapshot_from_run(_make_run(final_status="running", node_statuses={"n": "running"}), [])
    cur = _snapshot_from_run(
        _make_run(
            final_status="success",
            node_statuses={"n": "success"},
            ended_at=ended,
        ),
        [_make_node_log(node_id="n", status="success", ended_at=ended)],
    )
    events = _diff_events(prev, cur)
    names = [e for e, _ in events]
    # node_finished + run_status (final_status change) appear before the
    # terminal marker, which must always be last.
    assert names[-1] == "run_finished"
    assert "node_finished" in names
    finished = events[-1][1]
    assert finished["final_status"] == "success"
    assert finished["ended_at"] == ended.isoformat()


# ===========================================================================
# Integration tests — real app, real stream, terminal run (cannot hang)
# ===========================================================================


def _seed_terminal_run(session_factory: sessionmaker[Session]) -> str:
    """Seed a *terminal* run with two node logs. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
        "available_issues": [],
        "selected_issue_ids": [],
        "tests_generated": False,
        "test_files": [],
        "test_generation_errors": [],
        "max_attempts": 3,
        "attempt": 0,
        "tests_passed": False,
        "last_test_output": "",
        "modified_files": [],
        "implementation_notes": None,
        "verification_status": "pending",
        "verification_reason": None,
        "final_status": "success",
        "extensions": {},
    }
    with session_factory() as session:
        session.add(
            RunORM(
                id=run_id,
                project_id=None,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node=None,
                node_statuses={"select": "success", "implement": "success"},
                final_status="success",
                started_at=now,
                ended_at=now,
                tokens_used=320,
                cost_usd=0.006,
                created_at=now,
                updated_at=now,
            )
        )
        for node_id in ("select", "implement"):
            session.add(
                NodeExecutionLogORM(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    node_id=node_id,
                    agent_id="agent-1",
                    runtime_id="api-call",
                    started_at=now,
                    ended_at=now,
                    prompt_xml="<agent_prompt/>",
                    stdout="",
                    stderr="",
                    output_json={},
                    tokens_used=160,
                    cost_usd=0.003,
                    duration_ms=800,
                    status="success",
                    error_message=None,
                )
            )
        session.commit()
    return run_id


@pytest.fixture
def client_and_factory() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-sse-events-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="sse-events-secret-32-chars-pad!!",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        with app.state.session_factory() as session:
            user_orm = session.query(UserORM).filter(UserORM.email == "test@local.dev").one()
            user_orm.is_superuser = True
            session.commit()
            login = c.post(
                "/auth/jwt/login",
                data={"username": "test@local.dev", "password": "test-password-123"},
            )
            c.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield c, app.state.session_factory


def _read_sse_events(client: TestClient, url: str, *, max_lines: int = 200) -> list[str]:
    """Stream the SSE body and return raw lines, bounded so we never hang.

    A terminal run closes the stream immediately after ``run_finished``;
    the ``max_lines`` cap is pure belt-and-braces.
    """
    lines: list[str] = []
    with client.stream("GET", url) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            lines.append(line)
            if len(lines) >= max_lines:
                break
    return lines


def test_stream_terminal_run_emits_snapshot_and_finished(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_and_factory
    run_id = _seed_terminal_run(factory)

    lines = _read_sse_events(client, f"/runs/{run_id}/events")
    body = "\n".join(lines)

    assert "event: snapshot" in body
    assert "event: run_finished" in body

    # The snapshot data must carry the node_statuses we seeded.
    snapshot_data = _extract_event_data(lines, "snapshot")
    assert snapshot_data["run_id"] == run_id
    assert snapshot_data["node_statuses"] == {
        "select": "success",
        "implement": "success",
    }

    finished_data = _extract_event_data(lines, "run_finished")
    assert finished_data["run_id"] == run_id
    assert finished_data["final_status"] == "success"


def _extract_event_data(lines: list[str], event_name: str) -> dict[str, Any]:
    """Return the JSON ``data`` payload following ``event: <event_name>``."""
    for i, line in enumerate(lines):
        if line == f"event: {event_name}":
            data_line = lines[i + 1]
            assert data_line.startswith("data: ")
            return json.loads(data_line[len("data: ") :])  # type: ignore[no-any-return]
    raise AssertionError(f"event {event_name!r} not found in stream:\n" + "\n".join(lines))


def test_stream_missing_run_returns_404(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = client_and_factory
    response = client.get(f"/runs/{uuid.uuid4()}/events")
    assert response.status_code == 404


def test_stream_other_users_run_returns_404(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_and_factory
    # Register a second (non-admin) user and assign the seeded run to the
    # *admin* user. The second user must not be able to see it →
    # anti-enumeration 404. (Assigning a real user id keeps the FK valid.)
    other_token = register_and_login(client, "other@local.dev")
    run_id = _seed_terminal_run(factory)
    with factory() as session:
        admin = session.query(UserORM).filter_by(email="test@local.dev").one()
        run = session.get(RunORM, run_id)
        assert run is not None
        run.user_id = admin.id
        session.commit()

    response = client.get(
        f"/runs/{run_id}/events",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


def test_stream_unauthenticated_returns_401(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_and_factory
    run_id = _seed_terminal_run(factory)
    response = client.get(
        f"/runs/{run_id}/events",
        headers={"Authorization": ""},
    )
    assert response.status_code == 401


# ===========================================================================
# Integration tests — node_log streaming (#662, Phase 3b-1)
# ===========================================================================


def _seed_running_run(session_factory: sessionmaker[Session]) -> str:
    """Seed a RUNNING run with one in-flight node. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
        "available_issues": [],
        "selected_issue_ids": [],
        "tests_generated": False,
        "test_files": [],
        "test_generation_errors": [],
        "max_attempts": 3,
        "attempt": 0,
        "tests_passed": False,
        "last_test_output": "",
        "modified_files": [],
        "implementation_notes": None,
        "verification_status": "pending",
        "verification_reason": None,
        "final_status": "running",
        "extensions": {},
    }
    with session_factory() as session:
        session.add(
            RunORM(
                id=run_id,
                project_id=None,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node="implement",
                node_statuses={"implement": "running"},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return run_id


def _insert_chunk(
    session_factory: sessionmaker[Session],
    run_id: str,
    *,
    node_id: str,
    content: str,
) -> None:
    with session_factory() as session:
        repo.append_output_chunk(session, run_id=run_id, node_id=node_id, content=content)
        session.commit()


def _finalize(session_factory: sessionmaker[Session], run_id: str) -> None:
    with session_factory() as session:
        repo.finalize_run(session, run_id, final_status="success")
        session.commit()


def test_stream_emits_node_log_for_chunks_then_finishes(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """Chunks inserted *after* connect are streamed as node_log; flipping
    the run terminal drains remaining chunks then closes the stream.

    A background thread inserts chunks and finalises the run a beat after
    the client connects, so the stream cannot hang.
    """
    client, factory = client_and_factory
    run_id = _seed_running_run(factory)

    def _drive() -> None:
        # Give the generator a tick to read its connect cursor first.
        time.sleep(POLL_INTERVAL * 1.5)
        _insert_chunk(factory, run_id, node_id="implement", content="line 1\n")
        _insert_chunk(factory, run_id, node_id="implement", content="line 2\n")
        time.sleep(POLL_INTERVAL * 1.5)
        # Trailing chunk inserted right before finalize — must NOT be dropped.
        _insert_chunk(factory, run_id, node_id="implement", content="line 3\n")
        _finalize(factory, run_id)

    driver = threading.Thread(target=_drive, daemon=True)
    driver.start()
    try:
        lines = _read_sse_events(client, f"/runs/{run_id}/events", max_lines=400)
    finally:
        driver.join(timeout=10.0)

    body = "\n".join(lines)
    assert "event: node_log" in body
    assert "event: run_finished" in body

    logs = _extract_all_event_data(lines, "node_log")
    contents = [d["content"] for d in logs]
    assert contents == ["line 1\n", "line 2\n", "line 3\n"]
    for d in logs:
        assert d["run_id"] == run_id
        assert d["node_id"] == "implement"
        assert d["stream"] == "stdout"
    # seq is the monotonic chunk id and strictly increases.
    seqs = [int(d["seq"]) for d in logs]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    # node_log events must all precede the terminal run_finished frame.
    last_node_log = max(i for i, line in enumerate(lines) if line == "event: node_log")
    run_finished_idx = next(i for i, line in enumerate(lines) if line == "event: run_finished")
    assert last_node_log < run_finished_idx


def test_stream_does_not_replay_pre_connect_chunks(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """Chunks present *before* connect are not re-emitted as node_log; only
    a chunk inserted after connect is streamed. State is still reflected in
    the snapshot, not replayed as logs.
    """
    client, factory = client_and_factory
    run_id = _seed_running_run(factory)

    # Pre-connect history — should NOT be replayed.
    _insert_chunk(factory, run_id, node_id="implement", content="pre 1\n")
    _insert_chunk(factory, run_id, node_id="implement", content="pre 2\n")

    def _drive() -> None:
        time.sleep(POLL_INTERVAL * 1.5)
        _insert_chunk(factory, run_id, node_id="implement", content="post\n")
        _finalize(factory, run_id)

    driver = threading.Thread(target=_drive, daemon=True)
    driver.start()
    try:
        lines = _read_sse_events(client, f"/runs/{run_id}/events", max_lines=400)
    finally:
        driver.join(timeout=10.0)

    logs = _extract_all_event_data(lines, "node_log")
    contents = [d["content"] for d in logs]
    assert contents == ["post\n"], contents
    assert "pre 1\n" not in contents
    assert "pre 2\n" not in contents


def _extract_all_event_data(lines: list[str], event_name: str) -> list[dict[str, Any]]:
    """Return all JSON ``data`` payloads following ``event: <event_name>``."""
    out: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if line == f"event: {event_name}":
            data_line = lines[i + 1]
            assert data_line.startswith("data: ")
            out.append(json.loads(data_line[len("data: ") :]))
    return out
