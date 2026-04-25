"""CRUD tests for /pipelines endpoints."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-crud-pipelines-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _pipeline_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "TDD Pipeline",
        "description": "Test-driven implementation pipeline",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "select_task",
        "nodes": [
            {
                "id": "select_task",
                "agent_id": "task-selector-id",
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "verify",
                "agent_id": "verifier-id",
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "select_task"},
            {
                "id": "e2",
                "source": "select_task",
                "target": "verify",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": True,
                },
            },
            {"id": "e3", "source": "verify", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    payload.update(overrides)
    return payload


def test_create_pipeline_v1(client: TestClient) -> None:
    response = client.post("/pipelines", json=_pipeline_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "TDD Pipeline"
    assert body["version"] == 1
    assert body["entry_point"] == "select_task"
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 3


def test_get_pipeline_404(client: TestClient) -> None:
    response = client.get("/pipelines/nope")
    assert response.status_code == 404


def test_update_pipeline_creates_new_version(client: TestClient) -> None:
    created = client.post("/pipelines", json=_pipeline_payload()).json()
    pipeline_id = created["id"]

    updated_payload = _pipeline_payload(
        description="Updated description",
        nodes=[
            {"id": "n1", "agent_id": "a1", "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": "a2", "position": {"x": 100, "y": 0}},
            {"id": "n3", "agent_id": "a3", "position": {"x": 200, "y": 0}},
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "n2"},
            {"id": "e3", "source": "n2", "target": "n3"},
            {"id": "e4", "source": "n3", "target": "__end__"},
        ],
    )
    response = client.put(f"/pipelines/{pipeline_id}", json=updated_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 2
    assert body["description"] == "Updated description"
    assert len(body["nodes"]) == 3


def test_pipeline_version_history(client: TestClient) -> None:
    created = client.post("/pipelines", json=_pipeline_payload()).json()
    pipeline_id = created["id"]
    client.put(f"/pipelines/{pipeline_id}", json=_pipeline_payload(description="v2"))
    client.put(f"/pipelines/{pipeline_id}", json=_pipeline_payload(description="v3"))

    response = client.get(f"/pipelines/{pipeline_id}/versions")
    assert response.status_code == 200
    versions = response.json()
    assert [v["version"] for v in versions] == [1, 2, 3]


def test_archive_pipeline(client: TestClient) -> None:
    created = client.post("/pipelines", json=_pipeline_payload()).json()
    pipeline_id = created["id"]

    response = client.delete(f"/pipelines/{pipeline_id}")
    assert response.status_code == 204

    listing = client.get("/pipelines").json()
    assert all(p["id"] != pipeline_id for p in listing["items"])


def test_invalid_edge_condition_rejected(client: TestClient) -> None:
    bad = _pipeline_payload(
        edges=[
            {
                "id": "e1",
                "source": "a",
                "target": "b",
                "condition": {"type": "unknown_type", "field": "x"},
            }
        ],
    )
    response = client.post("/pipelines", json=bad)
    assert response.status_code == 422
