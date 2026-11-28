"""Per-node ``retry`` policy — schema, validation, attempt counter (#931).

Schema + validator + state only; the runtime routing lands in #932.
"""

from __future__ import annotations

from typing import Any

import pytest
from dap_types import PipelineState
from dap_types.pipeline import ComparisonCondition, PipelineNode, RetryPolicy
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


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
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _retry(**overrides: Any) -> dict[str, Any]:
    retry: dict[str, Any] = {
        "max_retries": 3,
        "retry_to": "coder",
        "on_failure": "escalate",
        "feedback_into": "test_feedback",
        "short_circuit_on": {
            "type": "comparison",
            "field": "final_status",
            "operator": "==",
            "value": "failed",
        },
    }
    retry.update(overrides)
    return retry


def _loop_payload(agent_id: str, retry: dict[str, Any] | None) -> dict[str, Any]:
    """coder → tester → __end__, with the retry block on the tester."""
    tester: dict[str, Any] = {"id": "tester", "agent_id": agent_id, "position": {"x": 1, "y": 0}}
    if retry is not None:
        tester["retry"] = retry
    return {
        "name": "Retry loop",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "coder",
        "nodes": [
            {"id": "coder", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
            tester,
            {"id": "escalate", "agent_id": agent_id, "position": {"x": 2, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "coder", "target": "tester"},
            {"id": "e2", "source": "tester", "target": "__end__"},
            {"id": "e3", "source": "escalate", "target": "__end__"},
        ],
        "defaults": {"max_attempts": 3, "budget_limit_usd": 5.0, "approval_required_nodes": []},
    }


def _validate(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/pipelines/validate", json=payload)
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_node_without_retry_defaults_to_none() -> None:
    node = PipelineNode(id="n1", agent_id="a1")
    assert node.retry is None


def test_retry_block_parses_full_shape() -> None:
    node = PipelineNode.model_validate({"id": "tester", "agent_id": "a1", "retry": _retry()})
    assert node.retry is not None
    assert node.retry.max_retries == 3
    assert node.retry.retry_to == "coder"
    assert node.retry.on_failure == "escalate"
    assert node.retry.feedback_into == "test_feedback"
    assert isinstance(node.retry.short_circuit_on, ComparisonCondition)
    assert PipelineNode.model_validate(node.model_dump()) == node


def test_retry_block_minimal_shape_defaults() -> None:
    policy = RetryPolicy.model_validate({"max_retries": 2, "retry_to": "coder"})
    assert policy.on_failure == "__end__"
    assert policy.feedback_into is None
    assert policy.short_circuit_on is None


def test_retry_block_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate({"max_retries": 2, "retry_to": "coder", "bogus": 1})


def test_retry_short_circuit_accepts_legacy_condition_shape() -> None:
    policy = RetryPolicy.model_validate(
        {
            "max_retries": 2,
            "retry_to": "coder",
            "short_circuit_on": {"field": "tests_passed", "op": "eq", "value": False},
        }
    )
    assert isinstance(policy.short_circuit_on, ComparisonCondition)
    assert policy.short_circuit_on.operator == "=="


def test_pipeline_state_has_node_attempts_default() -> None:
    state = PipelineState(run_id="r", repo="o/r", branch="main")
    assert state.node_attempts == {}


def test_old_state_snapshot_without_node_attempts_loads() -> None:
    old = PipelineState(run_id="r", repo="o/r", branch="main").model_dump()
    del old["node_attempts"]
    restored = PipelineState.model_validate(old)
    assert restored.node_attempts == {}


def test_pipeline_state_node_attempts_round_trips() -> None:
    state = PipelineState(run_id="r", repo="o/r", branch="main", node_attempts={"tester": 2})
    assert PipelineState.model_validate(state.model_dump()).node_attempts == {"tester": 2}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def test_validate_valid_retry_loop(client: TestClient) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry()))
    assert body["valid"] is True, body
    assert body["errors"] == []


def test_validate_without_retry_unchanged(client: TestClient) -> None:
    """``escalate`` is only reachable through ``on_failure`` — without the
    retry block it's unreachable, exactly as before #931."""
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, None))
    assert body["valid"] is False
    assert any("'escalate' is unreachable from START" in e for e in body["errors"])


def test_validate_retry_to_unknown_node(client: TestClient) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(retry_to="ghost")))
    assert body["valid"] is False
    assert any("retry.retry_to 'ghost'" in e for e in body["errors"]), body


def test_validate_retry_to_sentinel_rejected(client: TestClient) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(retry_to="__end__")))
    assert body["valid"] is False
    assert any("retry.retry_to '__end__'" in e for e in body["errors"]), body


def test_validate_on_failure_unknown_node(client: TestClient) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(on_failure="ghost")))
    assert body["valid"] is False
    assert any("retry.on_failure 'ghost'" in e for e in body["errors"]), body


def test_validate_on_failure_end_sentinel_allowed(client: TestClient) -> None:
    agent_id = _create_agent(client)
    payload = _loop_payload(agent_id, _retry(on_failure="__end__"))
    payload["nodes"] = [n for n in payload["nodes"] if n["id"] != "escalate"]
    payload["edges"] = [e for e in payload["edges"] if e["source"] != "escalate"]
    body = _validate(client, payload)
    assert body["valid"] is True, body


@pytest.mark.parametrize("max_retries", [0, -1, 21])
def test_validate_max_retries_out_of_range(client: TestClient, max_retries: int) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(max_retries=max_retries)))
    assert body["valid"] is False
    assert any("retry.max_retries" in e and "1-20" in e for e in body["errors"]), body


@pytest.mark.parametrize("max_retries", [1, 20])
def test_validate_max_retries_bounds_inclusive(client: TestClient, max_retries: int) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(max_retries=max_retries)))
    assert body["valid"] is True, body


def test_validate_retry_to_cannot_loop_back(client: TestClient) -> None:
    """``escalate`` only reaches ``__end__``, never ``tester`` again."""
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(retry_to="escalate")))
    assert body["valid"] is False
    assert any("cannot reach 'tester' again" in e for e in body["errors"]), body


def test_validate_retry_to_self_is_a_loop(client: TestClient) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(retry_to="tester")))
    assert body["valid"] is True, body


@pytest.mark.parametrize("key", ["", "extensions.test_feedback", "has space", "1abc"])
def test_validate_feedback_into_must_be_extensions_key(client: TestClient, key: str) -> None:
    agent_id = _create_agent(client)
    body = _validate(client, _loop_payload(agent_id, _retry(feedback_into=key)))
    assert body["valid"] is False
    assert any("retry.feedback_into" in e for e in body["errors"]), body


def test_validate_short_circuit_unknown_field_warns(client: TestClient) -> None:
    agent_id = _create_agent(client)
    short = {"type": "comparison", "field": "no_such_field", "operator": "==", "value": 1}
    body = _validate(client, _loop_payload(agent_id, _retry(short_circuit_on=short)))
    assert body["valid"] is True, body
    assert any(
        "Node 'tester' retry.short_circuit_on references unknown PipelineState field" in w
        for w in body["warnings"]
    ), body


def test_validate_short_circuit_malformed_is_422(client: TestClient) -> None:
    agent_id = _create_agent(client)
    short = {"type": "comparison", "field": "final_status", "operator": "~=", "value": 1}
    response = client.post(
        "/pipelines/validate", json=_loop_payload(agent_id, _retry(short_circuit_on=short))
    )
    assert response.status_code == 422


def test_create_rejects_invalid_retry(client: TestClient) -> None:
    agent_id = _create_agent(client)
    response = client.post("/pipelines", json=_loop_payload(agent_id, _retry(max_retries=99)))
    assert response.status_code == 422
    assert any("retry.max_retries" in e for e in response.json()["detail"]["errors"])


# ---------------------------------------------------------------------------
# Round-trip through create / export / import
# ---------------------------------------------------------------------------


def test_create_get_round_trips_retry(client: TestClient) -> None:
    agent_id = _create_agent(client)
    created = client.post("/pipelines", json=_loop_payload(agent_id, _retry()))
    assert created.status_code == 201, created.text
    fetched = client.get(f"/pipelines/{created.json()['id']}").json()
    tester = next(n for n in fetched["nodes"] if n["id"] == "tester")
    assert tester["retry"] == _retry()
    coder = next(n for n in fetched["nodes"] if n["id"] == "coder")
    assert coder.get("retry") is None


def test_export_import_round_trips_retry(client: TestClient) -> None:
    agent_id = _create_agent(client)
    created = client.post("/pipelines", json=_loop_payload(agent_id, _retry())).json()

    exported = client.get(f"/pipelines/{created['id']}/export")
    assert exported.status_code == 200, exported.text
    envelope = exported.json()
    tester = next(n for n in envelope["pipeline"]["nodes"] if n["id"] == "tester")
    assert tester["retry"] == _retry()

    imported = client.post("/pipelines/import", json=envelope)
    assert imported.status_code == 201, imported.text
    tester = next(n for n in imported.json()["nodes"] if n["id"] == "tester")
    assert tester["retry"] == _retry()


def test_bundle_export_import_round_trips_retry(client: TestClient) -> None:
    agent_id = _create_agent(client)
    created = client.post("/pipelines", json=_loop_payload(agent_id, _retry())).json()

    exported = client.get(f"/pipelines/{created['id']}/export", params={"bundle": "true"})
    assert exported.status_code == 200, exported.text
    imported = client.post("/pipelines/import", json=exported.json())
    assert imported.status_code == 201, imported.text
    tester = next(n for n in imported.json()["nodes"] if n["id"] == "tester")
    assert tester["retry"] == _retry()
