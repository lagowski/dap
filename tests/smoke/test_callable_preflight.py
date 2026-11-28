"""Fail-fast preflight for python-func callables at trigger time (#710).

A pipeline whose python-func node points at an unresolvable callable_path
(e.g. a missing dap-cortex) must be rejected with a clean 422 *before* the
run starts — not fail mid-run as a cryptic failed node.
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


def test_trigger_rejects_unresolvable_callable(client: TestClient) -> None:
    agent_id = _pf_agent(
        client,
        callable_path="dap_no_such_module_zzz_710:run",
        name="Missing Callable",
    )
    pipeline_id = _single_node_pipeline(client, agent_id, name="Broken Cortex-ish")

    resp = client.post("/runs", json={"pipeline_id": pipeline_id})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    # Names the offending node and explains the resolution failure.
    assert "n1" in detail
    assert "cannot import" in detail


def test_trigger_allows_resolvable_callable(client: TestClient) -> None:
    # ``json:dumps`` resolves (importable + callable); preflight must pass.
    # (The run may still fail later on the call signature — that's not the
    # preflight's job; the trigger must return 201.)
    agent_id = _pf_agent(client, callable_path="json:dumps", name="Resolvable")
    pipeline_id = _single_node_pipeline(client, agent_id, name="Resolvable Pipe")

    resp = client.post("/runs", json={"pipeline_id": pipeline_id})
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"]
