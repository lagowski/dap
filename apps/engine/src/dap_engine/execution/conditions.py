"""Evaluate EdgeCondition against PipelineState.

Pure function: no I/O, no randomness. Used by runner to pick conditional
edges deterministically.
"""

from __future__ import annotations

from typing import Any

from dap_types import PipelineState
from dap_types.pipeline import (
    ComparisonCondition,
    ComparisonOperator,
    EdgeCondition,
    LogicalCondition,
)


def evaluate_condition(condition: EdgeCondition, state: PipelineState) -> bool:
    """Evaluate the condition against state. Returns True/False deterministically.

    Unknown fields evaluate to False (the comparison cannot be made).
    """
    if isinstance(condition, ComparisonCondition):
        return _evaluate_comparison(condition, state)
    if isinstance(condition, LogicalCondition):
        if condition.type == "and":
            return all(evaluate_condition(c, state) for c in condition.children)
        if condition.type == "or":
            return any(evaluate_condition(c, state) for c in condition.children)
        msg = f"Unknown logical operator: {condition.type}"
        raise ValueError(msg)
    msg = f"Unknown condition type: {type(condition).__name__}"
    raise ValueError(msg)


def _evaluate_comparison(condition: ComparisonCondition, state: PipelineState) -> bool:
    state_dict = state.model_dump()
    actual = _get_field(state_dict, condition.field)
    if actual is _MISSING:
        return False
    return _apply_operator(actual, condition.operator, condition.value)


_MISSING = object()


def _get_field(data: dict[str, Any], field: str) -> Any:
    """Traverse *data* following dot-separated *field* path.

    Returns ``_MISSING`` sentinel when any segment is absent or a
    non-dict intermediate is encountered.
    """
    parts = field.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


_OPERATORS: dict[ComparisonOperator, Any] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _apply_operator(
    actual: Any,
    operator: ComparisonOperator,
    expected: str | int | float | bool | None,
) -> bool:
    fn = _OPERATORS.get(operator)
    if fn is None:
        msg = f"Unknown operator: {operator}"
        raise ValueError(msg)
    # Ordering ops require comparable types — fall back to False on TypeError.
    try:
        return bool(fn(actual, expected))
    except TypeError:
        return False
