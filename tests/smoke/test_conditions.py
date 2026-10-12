"""Unit tests for evaluate_condition — pure function, no I/O."""

from __future__ import annotations

import pytest
from dap_engine.execution.conditions import evaluate_condition
from dap_types import PipelineState
from dap_types.pipeline import ComparisonCondition, LogicalCondition, PipelineEdge
from pydantic import ValidationError


def _state(**overrides: object) -> PipelineState:
    base: dict[str, object] = {"run_id": "r-1", "repo": "test", "branch": "main"}
    base.update(overrides)
    return PipelineState.model_validate(base)


def test_eq_true() -> None:
    cond = ComparisonCondition(field="tests_passed", operator="==", value=True)
    assert evaluate_condition(cond, _state(tests_passed=True)) is True


def test_eq_false() -> None:
    cond = ComparisonCondition(field="tests_passed", operator="==", value=True)
    assert evaluate_condition(cond, _state(tests_passed=False)) is False


def test_neq() -> None:
    cond = ComparisonCondition(field="final_status", operator="!=", value="success")
    assert evaluate_condition(cond, _state()) is True  # default running != success


def test_lt() -> None:
    cond = ComparisonCondition(field="attempt", operator="<", value=3)
    assert evaluate_condition(cond, _state(attempt=2)) is True
    assert evaluate_condition(cond, _state(attempt=3)) is False


def test_gte() -> None:
    cond = ComparisonCondition(field="attempt", operator=">=", value=3)
    assert evaluate_condition(cond, _state(attempt=3)) is True


def test_unknown_field_returns_false() -> None:
    cond = ComparisonCondition(field="nonexistent_field", operator="==", value=True)
    assert evaluate_condition(cond, _state()) is False


def test_logical_and() -> None:
    cond = LogicalCondition(
        type="and",
        children=[
            ComparisonCondition(field="tests_passed", operator="==", value=False),
            ComparisonCondition(field="attempt", operator="<", value=3),
        ],
    )
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=1)) is True
    assert evaluate_condition(cond, _state(tests_passed=True, attempt=1)) is False
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=5)) is False


def test_logical_or() -> None:
    cond = LogicalCondition(
        type="or",
        children=[
            ComparisonCondition(field="tests_passed", operator="==", value=True),
            ComparisonCondition(field="attempt", operator=">=", value=3),
        ],
    )
    assert evaluate_condition(cond, _state(tests_passed=True, attempt=1)) is True
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=3)) is True
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=1)) is False


def test_nested_logical() -> None:
    # (tests_passed == True) AND ((attempt < 3) OR (final_status == "running"))
    cond = LogicalCondition(
        type="and",
        children=[
            ComparisonCondition(field="tests_passed", operator="==", value=True),
            LogicalCondition(
                type="or",
                children=[
                    ComparisonCondition(field="attempt", operator="<", value=3),
                    ComparisonCondition(field="final_status", operator="==", value="running"),
                ],
            ),
        ],
    )
    assert (
        evaluate_condition(cond, _state(tests_passed=True, attempt=5, final_status="running"))
        is True
    )
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=1)) is False


def test_type_mismatch_returns_false() -> None:
    """Comparing string field with int → no crash, returns False."""
    cond = ComparisonCondition(field="repo", operator=">", value=42)
    assert evaluate_condition(cond, _state(repo="my-repo")) is False


# ---------------------------------------------------------------------------
# Dot-notation traversal (extensions.* paths)
# ---------------------------------------------------------------------------


def test_dot_notation_extensions_review_status_eq() -> None:
    cond = ComparisonCondition(field="extensions.review_status", operator="==", value="clean")
    assert evaluate_condition(cond, _state(extensions={"review_status": "clean"})) is True


def test_dot_notation_extensions_review_status_eq_mismatch() -> None:
    cond = ComparisonCondition(field="extensions.review_status", operator="==", value="clean")
    assert evaluate_condition(cond, _state(extensions={"review_status": "needs_work"})) is False


def test_dot_notation_extensions_review_attempts_lt() -> None:
    cond = ComparisonCondition(field="extensions.review_attempts", operator="<", value=2)
    assert evaluate_condition(cond, _state(extensions={"review_attempts": 1})) is True
    assert evaluate_condition(cond, _state(extensions={"review_attempts": 2})) is False


def test_dot_notation_missing_nested_key_returns_false() -> None:
    cond = ComparisonCondition(field="extensions.nonexistent", operator="==", value="x")
    assert evaluate_condition(cond, _state(extensions={})) is False


def test_dot_notation_top_level_still_works() -> None:
    """Dot-notation doesn't break plain (non-dotted) field access."""
    cond = ComparisonCondition(field="tests_passed", operator="==", value=True)
    assert evaluate_condition(cond, _state(tests_passed=True)) is True


def test_dot_notation_review_approved_bool() -> None:
    """Bool comparisons work for extensions.review_approved (Phase 2 clean path)."""
    cond = ComparisonCondition(field="extensions.review_approved", operator="==", value=True)
    assert evaluate_condition(cond, _state(extensions={"review_approved": True})) is True
    assert evaluate_condition(cond, _state(extensions={"review_approved": False})) is False


def test_dot_notation_and_condition() -> None:
    """Compound: review_approved==false AND review_attempts<2 (Phase 2 retry edge)."""
    cond = LogicalCondition(
        type="and",
        children=[
            ComparisonCondition(field="extensions.review_approved", operator="==", value=False),
            ComparisonCondition(field="extensions.review_attempts", operator="<", value=2),
        ],
    )
    assert (
        evaluate_condition(
            cond, _state(extensions={"review_approved": False, "review_attempts": 0})
        )
        is True
    )
    assert (
        evaluate_condition(cond, _state(extensions={"review_approved": True, "review_attempts": 0}))
        is False
    )
    assert (
        evaluate_condition(
            cond, _state(extensions={"review_approved": False, "review_attempts": 2})
        )
        is False
    )


# ---------------------------------------------------------------------------
# Negation (#930)
# ---------------------------------------------------------------------------


def _not(child: ComparisonCondition | LogicalCondition) -> LogicalCondition:
    return LogicalCondition(type="not", children=[child])


def test_not_inverts_comparison() -> None:
    cond = _not(ComparisonCondition(field="tests_passed", operator="==", value=True))
    assert evaluate_condition(cond, _state(tests_passed=True)) is False
    assert evaluate_condition(cond, _state(tests_passed=False)) is True


def test_not_of_missing_path_is_true() -> None:
    """A missing path compares False, so its negation is True."""
    cond = _not(ComparisonCondition(field="extensions.absent", operator="==", value="x"))
    assert evaluate_condition(cond, _state(extensions={})) is True


def test_not_nested_in_and() -> None:
    # (attempt < 3) AND NOT (extensions.status == "fatal")
    cond = LogicalCondition(
        type="and",
        children=[
            ComparisonCondition(field="attempt", operator="<", value=3),
            _not(ComparisonCondition(field="extensions.status", operator="==", value="fatal")),
        ],
    )
    assert evaluate_condition(cond, _state(attempt=1, extensions={"status": "ok"})) is True
    assert evaluate_condition(cond, _state(attempt=1, extensions={"status": "fatal"})) is False
    assert evaluate_condition(cond, _state(attempt=5, extensions={"status": "ok"})) is False


def test_not_nested_in_or() -> None:
    cond = LogicalCondition(
        type="or",
        children=[
            ComparisonCondition(field="tests_passed", operator="==", value=True),
            _not(ComparisonCondition(field="attempt", operator="<", value=3)),
        ],
    )
    assert evaluate_condition(cond, _state(tests_passed=True, attempt=1)) is True
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=3)) is True
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=1)) is False


def test_not_wrapping_logical() -> None:
    """NOT (a AND b) == (NOT a) OR (NOT b)."""
    cond = _not(
        LogicalCondition(
            type="and",
            children=[
                ComparisonCondition(field="tests_passed", operator="==", value=True),
                ComparisonCondition(field="attempt", operator="<", value=3),
            ],
        ),
    )
    assert evaluate_condition(cond, _state(tests_passed=True, attempt=1)) is False
    assert evaluate_condition(cond, _state(tests_passed=False, attempt=1)) is True
    assert evaluate_condition(cond, _state(tests_passed=True, attempt=3)) is True


def test_double_not_is_identity() -> None:
    inner = ComparisonCondition(field="tests_passed", operator="==", value=True)
    cond = _not(_not(inner))
    assert evaluate_condition(cond, _state(tests_passed=True)) is True
    assert evaluate_condition(cond, _state(tests_passed=False)) is False


def test_not_parses_from_strict_dict() -> None:
    edge = PipelineEdge.model_validate(
        {
            "id": "e1",
            "source": "a",
            "target": "b",
            "condition": {
                "type": "not",
                "children": [
                    {"type": "comparison", "field": "tests_passed", "operator": "==", "value": True}
                ],
            },
        },
    )
    assert isinstance(edge.condition, LogicalCondition)
    assert edge.condition.type == "not"
    assert evaluate_condition(edge.condition, _state(tests_passed=False)) is True


@pytest.mark.parametrize("count", [0, 2])
def test_not_requires_exactly_one_child(count: int) -> None:
    child = ComparisonCondition(field="tests_passed", operator="==", value=True)
    with pytest.raises(ValidationError, match="exactly one child"):
        LogicalCondition(type="not", children=[child] * count)


@pytest.mark.parametrize("kind", ["and", "or"])
def test_and_or_still_accept_any_child_count(kind: str) -> None:
    """Regression: the single-child rule is ``not``-only."""
    child = ComparisonCondition(field="tests_passed", operator="==", value=True)
    for count in (0, 1, 3):
        LogicalCondition.model_validate({"type": kind, "children": [child] * count})


# ---------------------------------------------------------------------------
# Legacy Cortex-shaped conditions keep their meaning (#930 regression)
# ---------------------------------------------------------------------------


def _legacy_edge(condition: dict[str, object]) -> PipelineEdge:
    return PipelineEdge.model_validate(
        {"id": "e1", "source": "a", "target": "b", "condition": condition},
    )


def test_legacy_comparison_unchanged() -> None:
    edge = _legacy_edge({"field": "extensions.review_status", "op": "eq", "value": "clean"})
    assert edge.condition == ComparisonCondition(
        field="extensions.review_status", operator="==", value="clean"
    )
    assert evaluate_condition(edge.condition, _state(extensions={"review_status": "clean"}))
    assert not evaluate_condition(edge.condition, _state(extensions={"review_status": "x"}))


def test_legacy_nested_and_unchanged() -> None:
    edge = _legacy_edge(
        {
            "field": "extensions.review_approved",
            "op": "eq",
            "value": False,
            "and": {"field": "extensions.review_attempts", "op": "lt", "value": 2},
        },
    )
    assert edge.condition == LogicalCondition(
        type="and",
        children=[
            ComparisonCondition(field="extensions.review_approved", operator="==", value=False),
            ComparisonCondition(field="extensions.review_attempts", operator="<", value=2),
        ],
    )
    ok = _state(extensions={"review_approved": False, "review_attempts": 1})
    exhausted = _state(extensions={"review_approved": False, "review_attempts": 2})
    assert evaluate_condition(edge.condition, ok) is True
    assert evaluate_condition(edge.condition, exhausted) is False


def test_legacy_list_or_unchanged() -> None:
    edge = _legacy_edge(
        {
            "field": "tests_passed",
            "op": "eq",
            "value": True,
            "or": [{"field": "attempt", "op": "gte", "value": 3}],
        },
    )
    assert isinstance(edge.condition, LogicalCondition)
    assert edge.condition.type == "or"
    assert evaluate_condition(edge.condition, _state(tests_passed=False, attempt=3)) is True
    assert evaluate_condition(edge.condition, _state(tests_passed=False, attempt=1)) is False
