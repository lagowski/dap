"""Read-only tests for /runs endpoints. We seed runs directly in DB
since trigger endpoint isn't implemented until F5."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import (
    NodeExecutionLogORM,
    RunORM,
    StateSnapshotORM,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def _seed_run(session_factory: sessionmaker[Session], **overrides: Any) -> str:
    """Insert a run + 1 snapshot + 1 node log directly. Returns run_id."""
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
    }

    with session_factory() as session:
        run = RunORM(
            id=run_id,
            pipeline_id=str(overrides.get("pipeline_id", "pipe-1")),
            pipeline_version=int(overrides.get("pipeline_version", 1)),
            trigger_source=str(overrides.get("trigger_source", "cli")),
            initial_state=initial_state,
            current_node=None,
            node_statuses={},
            final_status=str(overrides.get("final_status", "running")),
            started_at=now,
            ended_at=None,
            tokens_used=0,
            cost_usd=0.0,
        )
        snapshot = StateSnapshotORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="select_task",
            timestamp=now,
            state={**initial_state, "selected_issue_ids": [42]},
        )
        log = NodeExecutionLogORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="select_task",
            agent_id="agent-1",
            runtime_id="api-call",
            started_at=now,
            ended_at=now,
            prompt_xml="<agent_prompt><role>task_selector</role></agent_prompt>",
            stdout="",
            stderr="",
            output_json={"selected_issue_ids": [42]},
            tokens_used=120,
            cost_usd=0.001,
            duration_ms=850,
            status="success",
            error_message=None,
        )
        session.add(run)
        session.add(snapshot)
        session.add(log)
        session.commit()

    return run_id


@pytest.fixture
def client_and_factory() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-crud-runs-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c, app.state.session_factory


def test_list_runs_empty(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = client_and_factory
    response = client.get("/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_runs_seeded(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    _seed_run(factory)
    _seed_run(factory, final_status="success")
    _seed_run(factory, pipeline_id="other-pipe")

    listing = client.get("/runs").json()
    assert listing["total"] == 3

    successful = client.get("/runs?final_status=success").json()
    assert successful["total"] == 1

    by_pipeline = client.get("/runs?pipeline_id=other-pipe").json()
    assert by_pipeline["total"] == 1


def test_get_run(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert body["pipeline_id"] == "pipe-1"


def test_get_run_404(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = client_and_factory
    response = client.get("/runs/nope")
    assert response.status_code == 404


def test_get_run_state(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/state")
    assert response.status_code == 200
    state = response.json()
    # Latest snapshot was set with selected_issue_ids=[42]
    assert state["selected_issue_ids"] == [42]


def test_state_history(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/state/history")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["run_id"] == run_id


def test_get_node_log(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/nodes/select_task")
    assert response.status_code == 200
    log = response.json()
    assert log["node_id"] == "select_task"
    assert log["agent_id"] == "agent-1"
    assert log["status"] == "success"
    assert log["tokens_used"] == 120


def test_get_node_log_404(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/nodes/missing_node")
    assert response.status_code == 404
