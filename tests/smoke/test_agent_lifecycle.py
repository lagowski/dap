"""Lifecycle tests for /agents — usage count + archive-when-referenced 409."""

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


def _create_agent(client: TestClient, name: str) -> str:
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


def _create_pipeline(client: TestClient, name: str, agent_ids: list[str]) -> str:
    nodes = [
        {"id": f"n{i}", "agent_id": aid, "position": {"x": i * 100, "y": 0}}
        for i, aid in enumerate(agent_ids)
    ]
    edges: list[dict[str, Any]] = [{"id": "e_start", "source": "__start__", "target": "n0"}]
    for i in range(len(agent_ids) - 1):
        edges.append({"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"})
    edges.append(
        {"id": "e_end", "source": f"n{len(agent_ids) - 1}", "target": "__end__"},
    )
    payload = {
        "name": name,
        "description": "lifecycle test",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n0",
        "nodes": nodes,
        "edges": edges,
        "defaults": {
            "max_attempts": 1,
            "budget_limit_usd": 1.0,
            "approval_required_nodes": [],
        },
    }
    response = client.post("/pipelines", json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_used_in_pipelines_zero_for_unused_agent(client: TestClient) -> None:
    _create_agent(client, "Lonely")
    items = client.get("/agents").json()["items"]
    assert len(items) == 1
    assert items[0]["used_in_pipelines"] == 0


def test_used_in_pipelines_counts_active_references(client: TestClient) -> None:
    aid = _create_agent(client, "Popular")
    _create_pipeline(client, "P1", [aid])
    _create_pipeline(client, "P2", [aid])
    items = client.get("/agents").json()["items"]
    assert items[0]["used_in_pipelines"] == 2


def test_used_in_pipelines_dedup_per_pipeline(client: TestClient) -> None:
    """Pipeline referencing the same agent in two nodes counts as one usage."""
    aid = _create_agent(client, "Twice")
    _create_pipeline(client, "P_twice", [aid, aid])
    items = client.get("/agents").json()["items"]
    assert items[0]["used_in_pipelines"] == 1


def test_used_in_pipelines_ignores_archived_pipelines(client: TestClient) -> None:
    aid = _create_agent(client, "Ghost")
    pid = _create_pipeline(client, "ToArchive", [aid])
    client.delete(f"/pipelines/{pid}")
    items = client.get("/agents").json()["items"]
    assert items[0]["used_in_pipelines"] == 0


def test_archive_agent_blocked_when_referenced(client: TestClient) -> None:
    aid = _create_agent(client, "InUse")
    pid = _create_pipeline(client, "Blocker", [aid])

    response = client.delete(f"/agents/{aid}")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert "1 active pipeline" in detail["message"]
    assert detail["blocking_pipelines"] == [{"id": pid, "name": "Blocker"}]

    # Agent still listed as active
    items = client.get("/agents").json()["items"]
    assert items[0]["id"] == aid


def test_archive_agent_succeeds_after_pipeline_archived(client: TestClient) -> None:
    aid = _create_agent(client, "FreeAfter")
    pid = _create_pipeline(client, "Holder", [aid])

    blocked = client.delete(f"/agents/{aid}")
    assert blocked.status_code == 409

    client.delete(f"/pipelines/{pid}")
    response = client.delete(f"/agents/{aid}")
    assert response.status_code == 204
