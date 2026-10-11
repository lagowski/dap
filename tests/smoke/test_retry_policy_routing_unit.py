"""Unit tests for ``dap_engine.execution.retry_policy`` (#932)."""

from __future__ import annotations

from dap_engine.execution.retry_policy import (
    apply_retry_policy,
    retry_recursion_limit,
    retry_route,
)
from dap_types import Pipeline, PipelineState
from dap_types.pipeline import RetryPolicy

FAILED = {"final_status": "failed", "verification_reason": "Node t failed: boom"}


def _state(**kw: object) -> PipelineState:
    return PipelineState(run_id="r", repo="x", branch="main", **kw)  # type: ignore[arg-type]


def _policy(**kw: object) -> RetryPolicy:
    return RetryPolicy.model_validate({"max_retries": 2, "retry_to": "c", **kw})


def test_pass_clears_counter_and_keeps_other_nodes() -> None:
    state = _state(node_attempts={"t": 2, "other": 1})
    diff = apply_retry_policy(node_id="t", policy=_policy(), state=state, state_diff={})
    assert diff["node_attempts"] == {"other": 1}
    assert retry_route(node_id="t", policy=_policy(), state=state.model_copy(update=diff)) == (
        "normal"
    )


def test_failure_with_retries_left_resets_status_and_writes_feedback() -> None:
    policy = _policy(feedback_into="fb")
    state = _state(extensions={"keep": 1})
    diff = apply_retry_policy(node_id="t", policy=policy, state=state, state_diff=FAILED)
    assert diff["node_attempts"] == {"t": 1}
    assert diff["final_status"] == "running"
    assert diff["extensions"] == {"keep": 1, "fb": "Node t failed: boom"}
    assert retry_route(node_id="t", policy=policy, state=state.model_copy(update=diff)) == "retry"


def test_failure_without_feedback_key_leaves_extensions_alone() -> None:
    diff = apply_retry_policy(node_id="t", policy=_policy(), state=_state(), state_diff=FAILED)
    assert "extensions" not in diff
    assert diff["final_status"] == "running"


def test_spent_keeps_failure_and_routes_to_on_failure() -> None:
    policy = _policy(feedback_into="fb")
    state = _state(node_attempts={"t": 2})
    diff = apply_retry_policy(node_id="t", policy=policy, state=state, state_diff=FAILED)
    assert diff["node_attempts"] == {"t": 3}
    assert diff["final_status"] == "failed"
    assert "extensions" not in diff
    assert retry_route(node_id="t", policy=policy, state=state.model_copy(update=diff)) == (
        "failure"
    )


def test_short_circuit_without_reason_uses_generic_feedback() -> None:
    policy = _policy(
        feedback_into="fb",
        short_circuit_on={
            "type": "comparison",
            "field": "tests_passed",
            "operator": "==",
            "value": False,
        },
    )
    diff = apply_retry_policy(node_id="t", policy=policy, state=_state(), state_diff={})
    assert diff["node_attempts"] == {"t": 1}
    assert diff["extensions"]["fb"] == "Node t matched retry.short_circuit_on"
    # The node itself didn't fail, so final_status is untouched.
    assert "final_status" not in diff


def _graph(*retries: int, nodes: int = 3) -> Pipeline:
    node_list = [{"id": f"n{i}", "agent_id": "a"} for i in range(nodes)]
    for i, max_retries in enumerate(retries):
        node_list[i]["retry"] = {"max_retries": max_retries, "retry_to": "n0"}
    return Pipeline.model_validate(
        {
            "id": "p",
            "name": "p",
            "version": 1,
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n0",
            "nodes": node_list,
            "edges": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def test_recursion_limit_without_retry_is_the_base() -> None:
    assert retry_recursion_limit(_graph(), 50) == 50


def test_recursion_limit_covers_every_retry_and_the_failure_tail() -> None:
    # 3 nodes x (21 attempts + on_failure pass) = 66 > the default 50.
    assert retry_recursion_limit(_graph(20), 50) == 66
    assert retry_recursion_limit(_graph(1), 50) == 50


def test_recursion_limit_multiplies_for_nested_loops() -> None:
    assert retry_recursion_limit(_graph(2, 3), 10) == 3 * (3 * 4 + 1)
