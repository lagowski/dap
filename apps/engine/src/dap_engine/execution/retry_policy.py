"""Runtime side of ``PipelineNode.retry`` (#932; schema + validation are #931).

Replaces cortex's ``routers._route_after_tester``. After a node with a
``retry`` block finishes, :func:`apply_retry_policy` folds the outcome into
the node's state diff, and the runner's router (:func:`retry_route`) reads
it back. All routing information lives in ``state.node_attempts[node]`` so
the decision survives a checkpoint and a manual rewind:

- ``0`` (or absent) — the last attempt passed: follow the normal edges.
- ``1..max_retries`` — failed with retries left: go to ``retry_to``.
- ``> max_retries`` — retries spent: go to ``on_failure``.
"""

from __future__ import annotations

from typing import Any, Literal

from dap_types import PipelineState
from dap_types.pipeline import RetryPolicy

from dap_engine.execution.conditions import evaluate_condition

RetryRoute = Literal["normal", "retry", "failure"]


def apply_retry_policy(
    *,
    node_id: str,
    policy: RetryPolicy,
    state: PipelineState,
    state_diff: dict[str, Any],
) -> dict[str, Any]:
    """Return ``state_diff`` with the retry bookkeeping for this attempt applied.

    ``state`` is the state the node ran on; ``state_diff`` is what the node
    produced. The attempt failed when the node failed (``final_status ==
    "failed"`` after the diff) or when ``short_circuit_on`` matches.
    """
    merged = state.model_copy(update=state_diff)
    node_failed = merged.final_status == "failed"
    short_circuited = policy.short_circuit_on is not None and evaluate_condition(
        policy.short_circuit_on, merged
    )
    attempts = dict(state.node_attempts)

    if not (node_failed or short_circuited):
        attempts.pop(node_id, None)
        return {**state_diff, "node_attempts": attempts}

    failures = attempts.get(node_id, 0) + 1
    attempts[node_id] = failures
    out = {**state_diff, "node_attempts": attempts}
    if failures > policy.max_retries:
        # Spent: leave final_status / verification_reason for on_failure.
        return out

    if policy.feedback_into is not None:
        out["extensions"] = {
            **merged.extensions,
            policy.feedback_into: _failure_reason(node_id, state_diff),
        }
    if node_failed:
        # Not terminal — the loop goes on through retry_to.
        out["final_status"] = "running"
    return out


def retry_route(*, node_id: str, policy: RetryPolicy, state: PipelineState) -> RetryRoute:
    """Where to go after ``node_id`` ran, from its ``node_attempts`` counter."""
    failures = state.node_attempts.get(node_id, 0)
    if failures == 0:
        return "normal"
    if failures <= policy.max_retries:
        return "retry"
    return "failure"


def _failure_reason(node_id: str, state_diff: dict[str, Any]) -> str:
    """Reason this attempt produced: its ``verification_reason``, else its test output."""
    for key in ("verification_reason", "last_test_output"):
        value = state_diff.get(key)
        if value:
            return str(value)
    return f"Node {node_id} matched retry.short_circuit_on"
