"""Read-only pre-run readiness for a pipeline's python-func callables (#710).

The trigger-time preflight rejects a run whose callable can't resolve; this is
its read-only counterpart — ``GET /pipelines/{id}/readiness`` lets the dashboard
show, before you run, which nodes can't run on this engine (e.g. dap-cortex not
installed). Same resolver, so the two agree.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def _pf_agent(client: TestClient, *, callable_path: str, name: str) -> str:
    resp = client.post(
        "/agents",
        json={
            "name": name,
            "role": "post_check",
            "runtime_id": "python-func",
            "runtime_config": {"callable_path": callable_path},
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            "input_schema": [],
            "output_schema": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _single_node_pipeline(client: TestClient, agent_id: str, *, name: str) -> str:
    resp = client.post(
        "/pipelines",
        json={
            "name": name,
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 1,
                "budget_limit_usd": 1.0,
                "approval_required_nodes": [],
                "requires_terminal_final_status": False,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_readiness_reports_a_resolvable_node_as_ready(client: TestClient) -> None:
    agent_id = _pf_agent(client, callable_path="json:dumps", name="Resolvable")
    pipeline_id = _single_node_pipeline(client, agent_id, name="Ready Pipe")

    resp = client.get(f"/pipelines/{pipeline_id}/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert len(body["checks"]) == 1
    check = body["checks"][0]
    assert check["node_id"] == "n1"
    assert check["callable_path"] == "json:dumps"
    assert check["resolvable"] is True
    assert check["error"] is None


def test_readiness_flags_an_unresolvable_node(client: TestClient) -> None:
    agent_id = _pf_agent(client, callable_path="dap_no_such_module_zzz_710:run", name="Missing")
    pipeline_id = _single_node_pipeline(client, agent_id, name="Broken Pipe")

    resp = client.get(f"/pipelines/{pipeline_id}/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is False
    check = body["checks"][0]
    assert check["resolvable"] is False
    assert "cannot import" in check["error"]


def test_readiness_404_for_unknown_pipeline(client: TestClient) -> None:
    resp = client.get("/pipelines/does-not-exist/readiness")
    assert resp.status_code == 404, resp.text
