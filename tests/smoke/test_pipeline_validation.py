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


# ---------------------------------------------------------------------------
# Cohesion checks (#59) — input/output contracts across the pipeline graph
# ---------------------------------------------------------------------------


def _create_agent_with_contract(
    client: TestClient,
    *,
    name: str,
    role: str = "task_selector",
    input_schema: list[str] | None = None,
    output_schema: list[str] | None = None,
) -> str:
    response = client.post(
        "/agents",
        json={
            "name": name,
            "role": role,
            "runtime_id": "api-call",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            "input_schema": input_schema or [],
            "output_schema": output_schema or [],
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _two_node_payload(
    *,
    a_id: str,
    b_id: str,
    name: str = "P",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": a_id, "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": b_id, "position": {"x": 100, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }


def test_cohesion_linear_chain_writer_satisfies_reader(client: TestClient) -> None:
    """A → B where A writes selected_issue_ids and B reads it: valid, no warning."""
    a = _create_agent_with_contract(
        client, name="Selector", output_schema=["selected_issue_ids"]
    )
    b = _create_agent_with_contract(
        client, name="Author", role="test_author", input_schema=["selected_issue_ids"]
    )
    body = client.post("/pipelines/validate", json=_two_node_payload(a_id=a, b_id=b)).json()
    assert body["valid"] is True
    assert body["errors"] == []
    # No "no downstream reader" warning either — B reads what A writes.
    assert all("no downstream node reads" not in w for w in body["warnings"])


def test_cohesion_missing_writer_fails(client: TestClient) -> None:
    """A → B where B reads selected_issue_ids but A doesn't write it → error."""
    a = _create_agent_with_contract(client, name="Empty")  # no output_schema
    b = _create_agent_with_contract(
        client, name="Author", role="test_author", input_schema=["selected_issue_ids"]
    )
    body = client.post("/pipelines/validate", json=_two_node_payload(a_id=a, b_id=b)).json()
    assert body["valid"] is False
    assert any(
        "selected_issue_ids" in e and "no upstream node writes it" in e
        for e in body["errors"]
    )


def test_cohesion_born_satisfied_by_non_trivial_default(client: TestClient) -> None:
    """``max_attempts: int = 3`` is born satisfied — no upstream writer needed."""
    a = _create_agent_with_contract(
        client, name="Reader", input_schema=["max_attempts"]
    )
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": a, "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    assert body["valid"] is True
    assert body["errors"] == []


def test_cohesion_born_satisfied_by_required_field(client: TestClient) -> None:
    """``run_id`` is required → comes from initial_state → satisfies any reader."""
    a = _create_agent_with_contract(client, name="Reader", input_schema=["run_id"])
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": a, "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    assert body["valid"] is True
    assert body["errors"] == []


def test_cohesion_trivial_default_does_not_satisfy(client: TestClient) -> None:
    """``selected_issue_ids: list[int] = Field(default_factory=list)`` is a sentinel.

    An agent that reads it without an upstream writer fails — empty list
    is "not yet populated", not a meaningful initial value.
    """
    a = _create_agent_with_contract(
        client, name="Reader", input_schema=["selected_issue_ids"]
    )
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": a, "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    assert body["valid"] is False
    assert any(
        "selected_issue_ids" in e and "no upstream node writes it" in e
        for e in body["errors"]
    )


def test_cohesion_conditional_branch_only_one_writer_fails(client: TestClient) -> None:
    """Diamond where only one branch writes the field → not a dominator → fail.

    Graph:
        entry → A (writes selected_issue_ids)
        entry → B (writes nothing)
        A → C, B → C (C reads selected_issue_ids)

    A doesn't dominate C (B path bypasses A) so C may run with the field
    unset — strict cohesion rejects this.
    """
    entry = _create_agent_with_contract(client, name="Entry")
    writer = _create_agent_with_contract(
        client, name="Writer", output_schema=["selected_issue_ids"]
    )
    bystander = _create_agent_with_contract(client, name="Bystander")
    reader = _create_agent_with_contract(
        client,
        name="Reader",
        role="test_author",
        input_schema=["selected_issue_ids"],
    )
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "entry",
        "nodes": [
            {"id": "entry", "agent_id": entry, "position": {"x": 0, "y": 0}},
            {"id": "a", "agent_id": writer, "position": {"x": 100, "y": -50}},
            {"id": "b", "agent_id": bystander, "position": {"x": 100, "y": 50}},
            {"id": "c", "agent_id": reader, "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "entry",
                "target": "a",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": True,
                },
            },
            {
                "id": "e2",
                "source": "entry",
                "target": "b",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": False,
                },
            },
            {"id": "e3", "source": "a", "target": "c"},
            {"id": "e4", "source": "b", "target": "c"},
            {"id": "e5", "source": "c", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    assert body["valid"] is False
    assert any(
        "'c'" in e and "selected_issue_ids" in e and "no upstream node writes it" in e
        for e in body["errors"]
    )


def test_cohesion_unused_output_warns(client: TestClient) -> None:
    """A writes selected_issue_ids but no downstream node reads it → warning."""
    a = _create_agent_with_contract(
        client, name="Writer", output_schema=["selected_issue_ids"]
    )
    b = _create_agent_with_contract(client, name="Bystander", role="test_author")
    body = client.post(
        "/pipelines/validate", json=_two_node_payload(a_id=a, b_id=b)
    ).json()
    assert body["valid"] is True
    assert any(
        "selected_issue_ids" in w and "no downstream node reads it" in w
        for w in body["warnings"]
    )


def test_cohesion_conflicting_writers_warns(client: TestClient) -> None:
    """Diamond where both branches write the same field → warning.

    Graph:
        entry → A (writes selected_issue_ids)
        entry → B (writes selected_issue_ids too)
        A, B → C

    Neither A dominates B nor vice versa — runtime merge order is undefined.
    """
    entry = _create_agent_with_contract(client, name="Entry")
    writer1 = _create_agent_with_contract(
        client, name="W1", output_schema=["selected_issue_ids"]
    )
    writer2 = _create_agent_with_contract(
        client, name="W2", output_schema=["selected_issue_ids"]
    )
    reader = _create_agent_with_contract(
        client,
        name="Reader",
        role="test_author",
        input_schema=["selected_issue_ids"],
    )
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "entry",
        "nodes": [
            {"id": "entry", "agent_id": entry, "position": {"x": 0, "y": 0}},
            {"id": "a", "agent_id": writer1, "position": {"x": 100, "y": -50}},
            {"id": "b", "agent_id": writer2, "position": {"x": 100, "y": 50}},
            {"id": "c", "agent_id": reader, "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "entry",
                "target": "a",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": True,
                },
            },
            {
                "id": "e2",
                "source": "entry",
                "target": "b",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": False,
                },
            },
            {"id": "e3", "source": "a", "target": "c"},
            {"id": "e4", "source": "b", "target": "c"},
            {"id": "e5", "source": "c", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    # Reader is satisfied (both branches write it) — but writers conflict.
    assert body["valid"] is True
    assert any(
        "selected_issue_ids" in w
        and "parallel branches" in w
        and "merge order is undefined" in w
        for w in body["warnings"]
    )


def test_cohesion_skipped_on_cycle(client: TestClient) -> None:
    """Cyclic pipeline (retry loop) → cohesion silently skipped, no false positives."""
    # A reads field that nobody writes — would normally fail. Add a back-edge
    # B → A to introduce a cycle so cohesion gives up gracefully.
    a = _create_agent_with_contract(
        client,
        name="Reader",
        input_schema=["selected_issue_ids"],
    )
    b = _create_agent_with_contract(
        client,
        name="Looper",
        role="test_author",
    )
    payload = {
        "name": "P",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": a, "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": b, "position": {"x": 100, "y": 0}},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n1",
                "target": "n2",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": True,
                },
            },
            {
                "id": "e2",
                "source": "n2",
                "target": "n1",
                "condition": {
                    "type": "comparison",
                    "field": "tests_passed",
                    "operator": "==",
                    "value": False,
                },
            },
            {"id": "e3", "source": "n2", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    body = client.post("/pipelines/validate", json=payload).json()
    # No cohesion errors raised because the graph has a cycle — strict
    # dominator semantics don't translate, so we skip rather than emit
    # false positives on retry-loop pipelines.
    assert body["valid"] is True
    assert all("no upstream node writes it" not in e for e in body["errors"])
