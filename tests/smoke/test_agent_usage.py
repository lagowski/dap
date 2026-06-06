"""Agent usage endpoint — which pipelines / projects use an agent (#697)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def _agent(client: TestClient, name: str = "Usage Agent") -> str:
    resp = client.post(
        "/agents",
        json={
            "name": name,
            "role": "test_author",
            "runtime_id": "claude-code",
            "runtime_config": {"model": "claude-sonnet-4-6", "max_turns": 10},
            "prompt_template": "<agent_prompt><role>test_author</role></agent_prompt>",
            # No input requirement → a single entry node validates with no
            # upstream writer needed.
            "input_schema": [],
            "output_schema": ["test_files"],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _pipeline(client: TestClient, agent_id: str, name: str = "Usage Pipeline") -> str:
    resp = client.post(
        "/pipelines",
        json={
            "name": name,
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [
                {"id": "e1", "source": "__start__", "target": "n1"},
                {"id": "e2", "source": "n1", "target": "__end__"},
            ],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _project(
    client: TestClient, pipeline_id: str, *, kind: str = "cortex", name: str = "Usage Project"
) -> str:
    resp = client.post(
        "/projects",
        json={
            "name": name,
            "description": "",
            "pipelines": {kind: pipeline_id},
            "env_vars": {},
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_usage_lists_pipeline_and_binding_project(client: TestClient) -> None:
    agent_id = _agent(client)
    pipeline_id = _pipeline(client, agent_id)
    project_id = _project(client, pipeline_id, kind="cortex")

    resp = client.get(f"/agents/{agent_id}/usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [p["id"] for p in body["pipelines"]] == [pipeline_id]
    assert len(body["projects"]) == 1
    proj = body["projects"][0]
    assert proj["id"] == project_id
    assert proj["bindings"] == [{"kind": "cortex", "pipeline_id": pipeline_id}]


def test_usage_empty_when_agent_unused(client: TestClient) -> None:
    agent_id = _agent(client, name="Lonely Agent")
    resp = client.get(f"/agents/{agent_id}/usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pipelines"] == []
    assert body["projects"] == []


def test_usage_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.get("/agents/00000000-0000-0000-0000-000000000000/usage")
    assert resp.status_code == 404, resp.text
