"""Tripwire tests for cross-run state bleed on ``GET /runs/{id}`` (#636).

These exercise the log-only observability tripwire added to ``get_run``:
when a run row is served in a terminal ``final_status`` while its task is
still active in the in-process ``RunRegistry`` (an impossible-but-observed
condition, #636), a WARNING is emitted. The tripwire must NEVER change the
response — same 200 status, same body — only the log differs.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution import RunRegistry
from dap_engine.persistence.models import RunORM, UserORM
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.smoke._auth import authed_test_client

WARNING_MARKER = "cross-run state bleed (#636)"


def _seed_run(session_factory: sessionmaker[Session], **overrides: Any) -> str:
    """Insert a minimal run row directly. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    final_status = str(overrides.get("final_status", "running"))
    terminal = final_status in {"success", "failed", "aborted"}
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
                node_statuses={},
                final_status=final_status,
                started_at=now,
                ended_at=now if terminal else None,
                tokens_used=0,
                cost_usd=0.0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return run_id


@pytest.fixture
def app_client_factory() -> Iterator[tuple[FastAPI, TestClient, sessionmaker[Session]]]:
    """Engine app + authenticated admin TestClient + session factory.

    Exposes the FastAPI ``app`` itself (unlike the conftest ``authed_client``)
    so tests can plant a real ``asyncio.Task`` into ``app.state.run_registry``
    to drive ``is_running`` True.
    """
    tmp = tempfile.mkdtemp(prefix="dap-tripwire-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="tripwire-secret-32-chars-padding",
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
        yield app, c, app.state.session_factory


@pytest.fixture
def force_running(
    app_client_factory: tuple[FastAPI, TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Factory: make ``RunRegistry.is_running`` report True for chosen run_ids.

    A real not-done ``asyncio.Task`` cannot be planted from the sync
    ``TestClient`` harness: it would live on a different event loop than the
    app's portal loop, and the app's lifespan shutdown (``RunRegistry.shutdown``)
    would then try to await a task attached to a foreign loop. Monkeypatching
    ``is_running`` — the exact signal ``get_run`` consults — exercises the
    tripwire deterministically without that cross-loop hazard.

    Returns a callable ``mark(run_id)``; only marked ids report running.
    """
    running_ids: set[str] = set()

    def _is_running(self: RunRegistry, run_id: str) -> bool:
        return run_id in running_ids

    monkeypatch.setattr(RunRegistry, "is_running", _is_running)

    def mark(run_id: str) -> None:
        running_ids.add(run_id)

    return mark


def test_tripwire_fires_for_terminal_run_with_active_task(
    app_client_factory: tuple[FastAPI, TestClient, sessionmaker[Session]],
    force_running: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Terminal run + is_running True → 200, body unchanged, WARNING logged."""
    _app, client, factory = app_client_factory
    run_id = _seed_run(factory, final_status="success")
    force_running(run_id)

    with caplog.at_level(logging.WARNING, logger="dap.engine.api.run_reads"):
        response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert body["final_status"] == "success"

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and WARNING_MARKER in r.getMessage()
    ]
    assert len(warnings) == 1, f"expected one tripwire warning, got {len(warnings)}"
    assert run_id in warnings[0].getMessage()


def test_tripwire_silent_when_task_not_running(
    app_client_factory: tuple[FastAPI, TestClient, sessionmaker[Session]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Terminal run + is_running False → 200, NO warning."""
    _app, client, factory = app_client_factory
    run_id = _seed_run(factory, final_status="success")

    with caplog.at_level(logging.WARNING, logger="dap.engine.api.run_reads"):
        response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["final_status"] == "success"
    assert not [r for r in caplog.records if WARNING_MARKER in r.getMessage()]


def test_tripwire_silent_when_non_terminal(
    app_client_factory: tuple[FastAPI, TestClient, sessionmaker[Session]],
    force_running: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-terminal run (running) + is_running True → 200, NO warning."""
    _app, client, factory = app_client_factory
    run_id = _seed_run(factory, final_status="running")
    force_running(run_id)

    with caplog.at_level(logging.WARNING, logger="dap.engine.api.run_reads"):
        response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["final_status"] == "running"
    assert not [r for r in caplog.records if WARNING_MARKER in r.getMessage()]
