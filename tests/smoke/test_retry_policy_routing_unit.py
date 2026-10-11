"""Unit tests for ``dap_engine.execution.retry_policy`` (#932)."""

from __future__ import annotations

from dap_engine.execution.retry_policy import apply_retry_policy, retry_route
from dap_types import PipelineState
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
