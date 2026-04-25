"""Tests for POST /pipelines/validate — DAG validation before save."""

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
    tmp = tempfile.mkdtemp(prefix="dap-validate-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _create_agent(client: TestClient, name: str = "Agent") -> str:
    response = client.post(
        "/agents",
        json={
            "name": name,
            "role": "task_selector",
            "runtime_id": "api-call",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _pipeline_payload(
    *,
    agent_id: str,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    entry_point: str = "n1",
    name: str = "Test",
) -> dict[str, Any]:
    if nodes is None:
        nodes = [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}]
    if edges is None:
        edges = [{"id": "e1", "source": "n1", "target": "__end__"}]
    return {
        "name": name,
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": entry_point,
        "nodes": nodes,
        "edges": edges,
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }


def test_validate_valid_pipeline(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post("/pipelines/validate", json=_pipeline_payload(agent_id=agent_id))
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["warnings"] == []


def test_validate_unknown_agent(client: TestClient) -> None:
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(agent_id="nonexistent-agent"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("Agent not found" in e for e in body["errors"])


def test_validate_archived_agent(client: TestClient) -> None:
    agent_id = _create_agent(client)
    archive = client.delete(f"/agents/{agent_id}")
    assert archive.status_code == 204

    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(agent_id=agent_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("archived" in e for e in body["errors"])


def test_validate_entry_point_missing(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(agent_id=agent_id, entry_point="nope"),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("entry_point" in e for e in body["errors"])


def test_validate_duplicate_node_ids(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n1", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[{"id": "e1", "source": "n1", "target": "__end__"}],
        ),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("Duplicate node ids" in e for e in body["errors"])


def test_validate_unknown_edge_target(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            edges=[{"id": "e1", "source": "n1", "target": "ghost"}],
        ),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("ghost" in e and "not a known node" in e for e in body["errors"])


def test_validate_unreachable_node(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            # n2 not connected — orphan
            edges=[{"id": "e1", "source": "n1", "target": "__end__"}],
        ),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("'n2' is unreachable" in e for e in body["errors"])


def test_validate_node_no_path_to_end(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            # n2 reachable but doesn't connect to END
            edges=[
                {"id": "e1", "source": "n1", "target": "n2"},
                {"id": "e2", "source": "n1", "target": "__end__"},
            ],
        ),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("'n2' has no path to END" in e for e in body["errors"])


def test_validate_ambiguous_routing(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            # Two unconditional edges from n1 — ambiguous
            edges=[
                {"id": "e1", "source": "n1", "target": "n2"},
                {"id": "e2", "source": "n1", "target": "__end__"},
                {"id": "e3", "source": "n2", "target": "__end__"},
            ],
        ),
    )
    body = response.json()
    assert body["valid"] is False
    assert any("ambiguous routing" in e.lower() for e in body["errors"])


def test_validate_unknown_condition_field_warns(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "__end__",
                    "condition": {
                        "type": "comparison",
                        "field": "totally_made_up_field",
                        "operator": "==",
                        "value": True,
                    },
                },
                {"id": "e2", "source": "n1", "target": "n2"},
                {"id": "e3", "source": "n2", "target": "__end__"},
            ],
        ),
    )
    body = response.json()
    # Should be valid — but with a warning about unknown field
    assert body["valid"] is True
    assert any("totally_made_up_field" in w for w in body["warnings"])


def test_validate_known_condition_field_no_warning(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "__end__",
                    "condition": {
                        "type": "comparison",
                        "field": "tests_passed",
                        "operator": "==",
                        "value": True,
                    },
                },
                {"id": "e2", "source": "n1", "target": "n2"},
                {"id": "e3", "source": "n2", "target": "__end__"},
            ],
        ),
    )
    body = response.json()
    assert body["valid"] is True
    assert body["warnings"] == []


def test_validate_logical_condition_with_unknown_field_warns(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post(
        "/pipelines/validate",
        json=_pipeline_payload(
            agent_id=agent_id,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "__end__",
                    "condition": {
                        "type": "and",
                        "children": [
                            {
                                "type": "comparison",
                                "field": "tests_passed",
                                "operator": "==",
                                "value": True,
                            },
                            {
                                "type": "comparison",
                                "field": "ghost_field",
                                "operator": "==",
                                "value": 42,
                            },
                        ],
                    },
                },
                {"id": "e2", "source": "n1", "target": "n2"},
                {"id": "e3", "source": "n2", "target": "__end__"},
            ],
        ),
    )
    body = response.json()
    assert body["valid"] is True
    assert any("ghost_field" in w for w in body["warnings"])


def test_validate_pydantic_error_returns_422(client: TestClient) -> None:
    """Malformed payload (missing required fields) → Pydantic 422, not validation result."""
    response = client.post("/pipelines/validate", json={"name": ""})
    assert response.status_code == 422
