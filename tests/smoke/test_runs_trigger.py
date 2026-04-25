"""E2E test for POST /runs — triggers pipeline execution via REST.

Stubs runtime adapter so no real LLM calls happen.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient


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
    assert body["final_status"] == "success"
    assert body["trigger_source"] == "api"
    assert body["ended_at"] is not None

    # Verify run was persisted + state snapshot recorded
    run_id = body["id"]
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
