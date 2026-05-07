"""Tests for node_statuses population from node_execution_logs.

Verifies that GET /runs/{id} computes node_statuses on the fly from
node_execution_logs rows rather than relying on the (historically empty)
JSON column on the runs table.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import NodeExecutionLogORM, RunORM
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

_INITIAL_STATE: dict[str, Any] = {
    "run_id": "",
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


def _seed_run_with_logs(
    factory: sessionmaker[Session],
    *,
    final_status: str = "running",
    logs: list[dict[str, Any]],
) -> str:
    """Insert a run with the given node execution logs. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    state = {**_INITIAL_STATE, "run_id": run_id}

    with factory() as session:
        run = RunORM(
            id=run_id,
            pipeline_id="pipe-test",
            pipeline_version=1,
            trigger_source="cli",
            initial_state=state,
            current_node=None,
            node_statuses={},
            final_status=final_status,
            started_at=now,
            ended_at=None,
            tokens_used=0,
            cost_usd=0.0,
        )
        session.add(run)
        for i, log_data in enumerate(logs):
            started = now + timedelta(seconds=i)
            log = NodeExecutionLogORM(
                id=str(uuid.uuid4()),
                run_id=run_id,
                node_id=log_data["node_id"],
                agent_id="agent-1",
                runtime_id="python-func",
                started_at=log_data.get("started_at", started),
                ended_at=log_data.get("started_at", started) + timedelta(milliseconds=100),
                prompt_xml="<agent_prompt/>",
                stdout="",
                stderr="",
                output_json=None,
                tokens_used=0,
                cost_usd=0.0,
                duration_ms=100,
                status=log_data["status"],
                error_message=None,
            )
            session.add(log)
        session.commit()

    return run_id


@pytest.fixture
def client_and_factory() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-node-statuses-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c, app.state.session_factory


def test_get_run_returns_populated_node_statuses(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """GET /runs/{id} returns node_statuses computed from node_execution_logs."""
    client, factory = client_and_factory
    run_id = _seed_run_with_logs(
        factory,
        logs=[{"node_id": "select_task", "status": "success"}],
    )

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["node_statuses"]["select_task"] == "success"


def test_get_run_node_statuses_latest_wins(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """When multiple logs exist for the same node_id, latest (by started_at) wins."""
    client, factory = client_and_factory
    now = datetime.now(UTC)
    run_id = _seed_run_with_logs(
        factory,
        logs=[
            {"node_id": "build", "status": "failed", "started_at": now},
            {"node_id": "build", "status": "success", "started_at": now + timedelta(seconds=10)},
        ],
    )

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["node_statuses"]["build"] == "success"


def test_get_run_mixed_node_statuses(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """A run with success/failed/skipped nodes returns the correct per-node status."""
    client, factory = client_and_factory
    run_id = _seed_run_with_logs(
        factory,
        final_status="failed",
        logs=[
            {"node_id": "mockup", "status": "success"},
            {"node_id": "specify", "status": "failed"},
            {"node_id": "review", "status": "skipped"},
        ],
    )

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    statuses = response.json()["node_statuses"]
    assert statuses["mockup"] == "success"
    assert statuses["specify"] == "failed"
    assert statuses["review"] == "skipped"


def test_node_status_updated_during_execution(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """In-flight (running) runs also show per-node progress via node_statuses."""
    client, factory = client_and_factory
    run_id = _seed_run_with_logs(
        factory,
        final_status="running",
        logs=[{"node_id": "select_task", "status": "success"}],
    )

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["final_status"] == "running"
    assert body["node_statuses"]["select_task"] == "success"
