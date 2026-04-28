"""E2E test for POST /runs — triggers pipeline execution via REST.

Stubs runtime adapter so no real LLM calls happen. POST /runs is async
(returns 202 with running status); we poll until completion.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 5.0


def _wait_for_completion(client: TestClient, run_id: str) -> dict[str, Any]:
    """Poll the run until final_status is not 'running'. Returns the run JSON."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        if body["final_status"] != "running":
            return body
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not complete within {POLL_TIMEOUT_S}s")


class TriggerStubAdapter:
    id = "trigger-stub"
    display_name = "Trigger Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, _task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(success=True, output="executed", duration_ms=1)


@pytest.fixture
def client_with_stub() -> Iterator[tuple[TestClient, RuntimeRegistry]]:
    tmp = tempfile.mkdtemp(prefix="dap-trigger-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(TriggerStubAdapter())
        yield c, registry


def _create_agent(client: TestClient) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Trigger Test",
            "role": "task_selector",
            "runtime_id": "trigger-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str) -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": "Trigger Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_trigger_run_e2e(client_with_stub: tuple[TestClient, RuntimeRegistry]) -> None:
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {"repo": "demo", "branch": "main"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["pipeline_id"] == pipeline_id
    assert body["pipeline_version"] == 1
    # Async — initial response is "running"
    assert body["final_status"] == "running"
    assert body["trigger_source"] == "api"

    run_id = body["id"]
    # Poll until complete
    completed = _wait_for_completion(client, run_id)
    assert completed["final_status"] == "success"
    assert completed["ended_at"] is not None

    # Verify run was persisted + state snapshot recorded
    history = client.get(f"/runs/{run_id}/state/history").json()
    assert len(history) >= 1

    # Node log was recorded
    node_log = client.get(f"/runs/{run_id}/nodes/n1").json()
    assert node_log["node_id"] == "n1"
    assert node_log["status"] == "success"


def test_trigger_run_404_unknown_pipeline(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _registry = client_with_stub
    response = client.post(
        "/runs",
        json={"pipeline_id": "nonexistent", "initial_state": {}},
    )
    assert response.status_code == 404


def test_trigger_run_extra_field_rejected(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _registry = client_with_stub
    response = client.post(
        "/runs",
        json={"pipeline_id": "anything", "extra": "field"},
    )
    assert response.status_code == 422


def test_trigger_run_invalid_initial_state(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            # max_attempts must be int, not string
            "initial_state": {"max_attempts": "not-a-number"},
        },
    )
    assert response.status_code == 422


def test_trigger_run_specific_version(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    # Update pipeline → creates v2
    update_payload: dict[str, Any] = {
        "name": "Trigger Pipeline v2",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
        "defaults": {"max_attempts": 3, "budget_limit_usd": 5.0, "approval_required_nodes": []},
    }
    update_response = client.put(f"/pipelines/{pipeline_id}", json=update_payload)
    assert update_response.status_code == 200

    # Trigger v1 explicitly
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "pipeline_version": 1, "initial_state": {}},
    )
    assert response.status_code == 201
    assert response.json()["pipeline_version"] == 1

    # Default → current (v2)
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    )
    assert response.status_code == 201
    assert response.json()["pipeline_version"] == 2


# ---------------------------------------------------------------------------
# project_id wiring (#64)
# ---------------------------------------------------------------------------


def _create_project(client: TestClient, **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "name": "Project for run tests",
        "working_directory": "/tmp/p",
    }
    payload.update(overrides)
    response = client.post("/projects", json=payload)
    assert response.status_code == 201
    return str(response.json()["id"])


def test_trigger_run_without_project_keeps_id_null(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    )
    assert response.status_code == 201
    assert response.json()["project_id"] is None


def test_trigger_run_stamps_project_id(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client)
    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "project_id": project_id,
            "initial_state": {},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == project_id

    # Round-trips via GET /runs/{id} too.
    fetched = client.get(f"/runs/{body['id']}").json()
    assert fetched["project_id"] == project_id


def test_trigger_run_unknown_project_returns_422(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "project_id": "ghost-project",
            "initial_state": {},
        },
    )
    assert response.status_code == 422
    assert "Project not found" in str(response.json()["detail"])


def test_trigger_run_archived_project_returns_422(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client)
    archive = client.delete(f"/projects/{project_id}")
    assert archive.status_code == 204

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "project_id": project_id,
            "initial_state": {},
        },
    )
    assert response.status_code == 422
    assert "archived" in str(response.json()["detail"])


def test_list_runs_filter_by_project(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """Three runs across two projects + one ad-hoc; filter must scope correctly."""
    client, _ = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    project_a = _create_project(client, name="A")
    project_b = _create_project(client, name="B")

    def _fire(project_id: str | None) -> str:
        body: dict[str, Any] = {"pipeline_id": pipeline_id, "initial_state": {}}
        if project_id is not None:
            body["project_id"] = project_id
        run_id = str(client.post("/runs", json=body).json()["id"])
        # Wait for completion before firing the next run — SQLite locks
        # if multiple background tasks finalise concurrently with the
        # test thread's writes.
        _wait_for_completion(client, run_id)
        return run_id

    run_a1 = _fire(project_a)
    run_a2 = _fire(project_a)
    run_b1 = _fire(project_b)
    run_adhoc = _fire(None)

    # No filter → all four
    listing = client.get("/runs").json()
    ids = {r["id"] for r in listing["items"]}
    assert {run_a1, run_a2, run_b1, run_adhoc}.issubset(ids)

    # Specific project A → only its runs
    listing_a = client.get(f"/runs?project_id={project_a}").json()
    ids_a = {r["id"] for r in listing_a["items"]}
    assert ids_a == {run_a1, run_a2}
    assert listing_a["total"] == 2

    # Specific project B → only its runs
    listing_b = client.get(f"/runs?project_id={project_b}").json()
    ids_b = {r["id"] for r in listing_b["items"]}
    assert ids_b == {run_b1}

    # Literal "null" → only ad-hoc runs
    listing_null = client.get("/runs?project_id=null").json()
    ids_null = {r["id"] for r in listing_null["items"]}
    assert ids_null == {run_adhoc}
    assert listing_null["total"] == 1


def test_list_runs_unknown_project_returns_empty(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """Filtering by an unknown project_id is harmless — returns empty page."""
    client, _ = client_with_stub
    listing = client.get("/runs?project_id=does-not-exist").json()
    assert listing["items"] == []
    assert listing["total"] == 0
