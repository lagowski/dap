"""Tests for #635 — backward-compat normalizer for cortex legacy edge-condition shape.

Every test in this module intentionally feeds a raw ``dict[str, object]``
to ``PipelineEdge(condition=...)`` so the Pydantic ``mode="before"``
validator (``_accept_legacy_condition_shape``) can rewrite the legacy
cortex bundle shape into the strict discriminated union. The strict
parameter type would reject those dicts at the *type* level even though
the validator accepts them at runtime — disable that single mypy code
file-wide rather than peppering every test with an inline ignore.
"""

# mypy: disable-error-code="arg-type"

from dap_types.pipeline import (
    ComparisonCondition,
    LogicalCondition,
    PipelineEdge,
)


def test_strict_comparison_passes_through_unchanged() -> None:
    """The canonical strict shape must still round-trip without modification."""
    edge = PipelineEdge(
        id="e1",
        source="a",
        target="b",
        condition={
            "type": "comparison",
            "field": "extensions.review_approved",
            "operator": "==",
            "value": True,
        },
    )
    assert isinstance(edge.condition, ComparisonCondition)
    assert edge.condition.field == "extensions.review_approved"
    assert edge.condition.operator == "=="
    assert edge.condition.value is True


def test_legacy_comparison_eq_shape_is_normalized() -> None:
    """Cortex bundle shape: {field, op, value} with op='eq' should become
    {type, field, operator='==', value}.
    """
    edge = PipelineEdge(
        id="e1",
        source="a",
        target="b",
        condition={
            "field": "extensions.review_approved",
            "op": "eq",
            "value": True,
        },
    )
    assert isinstance(edge.condition, ComparisonCondition)
    assert edge.condition.operator == "=="
    assert edge.condition.value is True


def test_legacy_operator_aliases_all_map() -> None:
    """All cortex short operator aliases (eq/ne/lt/lte/gt/gte) map to DAP symbols."""
    expected = {"eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
    for legacy_op, strict_op in expected.items():
        edge = PipelineEdge(
            id=f"e-{legacy_op}",
            source="a",
            target="b",
            condition={"field": "x", "op": legacy_op, "value": 1},
        )
        assert isinstance(edge.condition, ComparisonCondition)
        assert edge.condition.operator == strict_op


def test_legacy_compound_and_is_normalized_to_logical_with_two_children() -> None:
    """The cortex compound shape with nested 'and' becomes a LogicalCondition with two children."""
    edge = PipelineEdge(
        id="e1",
        source="a",
        target="b",
        condition={
            "field": "extensions.review_approved",
            "op": "eq",
            "value": False,
            "and": {
                "field": "extensions.review_attempts",
                "op": "lt",
                "value": 2,
            },
        },
    )
    assert isinstance(edge.condition, LogicalCondition)
    assert edge.condition.type == "and"
    assert len(edge.condition.children) == 2
    first, second = edge.condition.children
    assert isinstance(first, ComparisonCondition)
    assert first.field == "extensions.review_approved"
    assert first.operator == "=="
    assert first.value is False
    assert isinstance(second, ComparisonCondition)
    assert second.field == "extensions.review_attempts"
    assert second.operator == "<"
    assert second.value == 2


def test_legacy_compound_or_works_the_same_way() -> None:
    """Nested 'or' key produces a LogicalCondition with type='or'."""
    edge = PipelineEdge(
        id="e1",
        source="a",
        target="b",
        condition={
            "field": "x",
            "op": "eq",
            "value": 1,
            "or": {"field": "y", "op": "eq", "value": 2},
        },
    )
    assert isinstance(edge.condition, LogicalCondition)
    assert edge.condition.type == "or"
    assert len(edge.condition.children) == 2


def test_none_condition_passes_through() -> None:
    """An edge with no condition stays None."""
    edge = PipelineEdge(id="e1", source="a", target="b", condition=None)
    assert edge.condition is None


def test_legacy_three_way_nested_and_chains_correctly() -> None:
    """Deeply-nested cortex shape: outer + nested + nested-inside-nested
    all become LogicalConditions.
    """
    edge = PipelineEdge(
        id="e1",
        source="a",
        target="b",
        condition={
            "field": "x",
            "op": "eq",
            "value": 1,
            "and": {
                "field": "y",
                "op": "eq",
                "value": 2,
                "and": {"field": "z", "op": "eq", "value": 3},
            },
        },
    )
    assert isinstance(edge.condition, LogicalCondition)
    assert edge.condition.type == "and"
    assert len(edge.condition.children) == 2
    # The second child is itself a nested LogicalCondition for (y AND z)
    nested = edge.condition.children[1]
    assert isinstance(nested, LogicalCondition)
    assert nested.type == "and"
    assert len(nested.children) == 2
