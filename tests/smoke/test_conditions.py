"""Unit tests for evaluate_condition — pure function, no I/O."""

from __future__ import annotations

from dap_engine.execution.conditions import evaluate_condition
from dap_types import PipelineState
from dap_types.pipeline import ComparisonCondition, LogicalCondition


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
    assert evaluate_condition(cond, _state(extensions={"review_approved": False, "review_attempts": 0})) is True
    assert evaluate_condition(cond, _state(extensions={"review_approved": True, "review_attempts": 0})) is False
    assert evaluate_condition(cond, _state(extensions={"review_approved": False, "review_attempts": 2})) is False
