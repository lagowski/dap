"""What a python-func agent's callable does — its docstring (#747).

Surfaces the resolved callable's ``inspect.getdoc`` so managed/cortex agents
(whose prompt is inert) can show a grounded "what this does".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def _agent(
    client: TestClient, *, runtime_id: str, runtime_config: dict[str, Any], name: str
) -> str:
    resp = client.post(
        "/agents",
        json={
            "name": name,
            "role": "post_check",
            "runtime_id": runtime_id,
            "runtime_config": runtime_config,
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            "input_schema": [],
            "output_schema": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_returns_the_callable_docstring(client: TestClient) -> None:
    # ``json.dumps`` resolves and has a docstring — stand-in for a cortex node.
    agent_id = _agent(
        client,
        runtime_id="python-func",
        runtime_config={"callable_path": "json:dumps"},
        name="Doc Agent",
    )
    resp = client.get(f"/agents/{agent_id}/callable-info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["callable_path"] == "json:dumps"
    assert body["resolvable"] is True
    assert body["error"] is None
    assert body["doc"] and "JSON" in body["doc"]  # json.dumps docstring mentions JSON


def test_reports_an_unresolvable_callable(client: TestClient) -> None:
    agent_id = _agent(
        client,
        runtime_id="python-func",
        runtime_config={"callable_path": "dap_no_such_module_zzz_747:run"},
        name="Broken Agent",
    )
    resp = client.get(f"/agents/{agent_id}/callable-info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolvable"] is False
    assert body["doc"] is None
    assert "cannot import" in body["error"]


def test_empty_for_non_python_func_agent(client: TestClient) -> None:
    agent_id = _agent(
        client,
        runtime_id="bash",
        runtime_config={"shell": "/bin/bash", "command": "echo hi"},
        name="Bash Agent",
    )
    resp = client.get(f"/agents/{agent_id}/callable-info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"callable_path": None, "resolvable": False, "doc": None, "error": None}


def test_404_for_unknown_agent(client: TestClient) -> None:
    resp = client.get("/agents/does-not-exist/callable-info")
    assert resp.status_code == 404, resp.text
