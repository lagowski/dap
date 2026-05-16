"""CRUD tests for /pipelines endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    """Authed TestClient shim — see ``tests/smoke/conftest.py::authed_client``.

    Kept as a local alias because most test bodies in this file already
    take a ``client: TestClient`` parameter; renaming them all would
    bloat the X1 diff without changing behaviour.
    """
    return authed_client


def _create_minimal_agent(client: TestClient, name: str) -> str:
    """Create an agent that the DAG validator will accept everywhere.

    Empty ``input_schema`` skips cohesion checks (legacy mode), so a
    pipeline using only these agents won't trigger ``no upstream
    writer`` errors regardless of how nodes are wired. Real production
    agents would declare schemas; the CRUD tests don't need to."""
    response = client.post(
        "/agents",
        json={
            "name": name,
            "role": "task_selector",
            "runtime_id": "api-call",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture
def agents(client: TestClient) -> dict[str, str]:
    """Pre-create the agents the pipeline payloads reference. Returns a
    map of human-readable label → server-assigned id so ``_pipeline_payload``
    can wire them in."""
    return {
        "selector": _create_minimal_agent(client, "Task Selector"),
        "verifier": _create_minimal_agent(client, "Verifier"),
        "extra": _create_minimal_agent(client, "Extra Agent"),
    }


def _pipeline_payload(agents: dict[str, str], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "TDD Pipeline",
        "description": "Test-driven implementation pipeline",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "select_task",
        "nodes": [
            {
                "id": "select_task",
                "agent_id": agents["selector"],
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "verify",
                "agent_id": agents["verifier"],
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


def test_create_pipeline_v1(client: TestClient, agents: dict[str, str]) -> None:
    response = client.post("/pipelines", json=_pipeline_payload(agents))
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


def test_update_pipeline_creates_new_version(
    client: TestClient,
    agents: dict[str, str],
) -> None:
    created = client.post("/pipelines", json=_pipeline_payload(agents)).json()
    pipeline_id = created["id"]

    updated_payload = _pipeline_payload(
        agents,
        description="Updated description",
        entry_point="n1",
        nodes=[
            {"id": "n1", "agent_id": agents["selector"], "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": agents["verifier"], "position": {"x": 100, "y": 0}},
            {"id": "n3", "agent_id": agents["extra"], "position": {"x": 200, "y": 0}},
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


def test_pipeline_version_history(
    client: TestClient,
    agents: dict[str, str],
) -> None:
    created = client.post("/pipelines", json=_pipeline_payload(agents)).json()
    pipeline_id = created["id"]
    client.put(
        f"/pipelines/{pipeline_id}",
        json=_pipeline_payload(agents, description="v2"),
    )
    client.put(
        f"/pipelines/{pipeline_id}",
        json=_pipeline_payload(agents, description="v3"),
    )

    response = client.get(f"/pipelines/{pipeline_id}/versions")
    assert response.status_code == 200
    versions = response.json()
    assert [v["version"] for v in versions] == [1, 2, 3]


def test_archive_pipeline(client: TestClient, agents: dict[str, str]) -> None:
    created = client.post("/pipelines", json=_pipeline_payload(agents)).json()
    pipeline_id = created["id"]

    response = client.delete(f"/pipelines/{pipeline_id}")
    assert response.status_code == 204

    listing = client.get("/pipelines").json()
    assert all(p["id"] != pipeline_id for p in listing["items"])


def test_invalid_edge_condition_rejected(
    client: TestClient,
    agents: dict[str, str],
) -> None:
    bad = _pipeline_payload(
        agents,
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


# ---------------------------------------------------------------------------
# Validation enforcement on save (#120)
# ---------------------------------------------------------------------------


def test_create_rejects_pipeline_with_unknown_agent_id(client: TestClient) -> None:
    """No agent in DB → validator's ``_check_agents`` produces an error
    → 422 from the new save-time enforcement."""
    payload = {
        "name": "Bad Pipeline",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": "ghost-agent", "position": {"x": 0, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    response = client.post("/pipelines", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "errors" in detail
    assert any("ghost-agent" in err for err in detail["errors"])


def test_update_rejects_invalid_dag_keeps_existing_version(
    client: TestClient,
    agents: dict[str, str],
) -> None:
    """A failed update must not create a v2 — the original pipeline
    keeps its current version."""
    created = client.post("/pipelines", json=_pipeline_payload(agents)).json()
    pipeline_id = created["id"]

    bad_update = _pipeline_payload(
        agents,
        nodes=[
            {"id": "n1", "agent_id": "ghost-agent", "position": {"x": 0, "y": 0}},
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        entry_point="n1",
    )
    response = client.put(f"/pipelines/{pipeline_id}", json=bad_update)
    assert response.status_code == 422

    versions = client.get(f"/pipelines/{pipeline_id}/versions").json()
    assert [v["version"] for v in versions] == [1]


def test_update_unknown_pipeline_returns_404_even_with_invalid_body(
    client: TestClient,
) -> None:
    """Existence wins over validation. A typo in the URL plus a typo
    in the body should still surface as 404 (the real cause), not 422
    (a misleading red herring about the body)."""
    bad_payload = {
        "name": "Bad",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": "ghost", "position": {"x": 0, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    response = client.put("/pipelines/no-such-pipeline", json=bad_payload)
    assert response.status_code == 404


def test_validate_endpoint_still_returns_200_for_invalid(
    client: TestClient,
) -> None:
    """``POST /pipelines/validate`` keeps its existing contract — 200
    with ``valid: false`` on invalid DAGs (live-feedback surface for
    the Designer). The save endpoints turn the same errors into 422."""
    payload = {
        "name": "Bad Pipeline",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": "ghost-agent", "position": {"x": 0, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    response = client.post("/pipelines/validate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("ghost-agent" in err for err in body["errors"])
