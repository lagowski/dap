"""Pure tests for the validator's edge-condition field check (#930).

No DB: ``_check_condition_fields`` only looks at the payload, so these
run without the API client.
"""

from __future__ import annotations

from typing import Any

import pytest
from dap_engine.contracts import PipelineCreate
from dap_engine.execution.validator import _check_condition_fields


def _payload(condition: dict[str, Any]) -> PipelineCreate:
    return PipelineCreate.model_validate(
        {
            "name": "Test",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": "a1", "position": {"x": 0, "y": 0}}],
            "edges": [
                {"id": "e1", "source": "n1", "target": "__end__", "condition": condition},
            ],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )


def _cmp(field: str) -> dict[str, Any]:
    return {"type": "comparison", "field": field, "operator": "==", "value": True}


@pytest.mark.parametrize(
    "field",
    [
        "tests_passed",
        "extensions",
        "extensions.foo",
        "extensions.foo.bar",
        "extensions.runtime.github.pr.state",
        "verification_status.anything",
    ],
)
def test_known_roots_do_not_warn(field: str) -> None:
    assert _check_condition_fields(_payload(_cmp(field))) == []


@pytest.mark.parametrize(
    "field",
    [
        "nonexistent_top_level",
        "nonexistent_top_level.child",
        "extensionsfoo",
        "Extensions.foo",
        "extensions.",
        "extensions..foo",
        "",
    ],
)
def test_unknown_roots_still_warn(field: str) -> None:
    warnings = _check_condition_fields(_payload(_cmp(field)))
    assert warnings == [f"Edge 'e1' references unknown PipelineState field: '{field}'"]


def test_fields_inside_not_are_checked() -> None:
    cond = {
        "type": "and",
        "children": [
            _cmp("extensions.ok"),
            {"type": "not", "children": [_cmp("ghost_field")]},
        ],
    }
    warnings = _check_condition_fields(_payload(cond))
    assert warnings == ["Edge 'e1' references unknown PipelineState field: 'ghost_field'"]


def test_not_with_known_field_does_not_warn() -> None:
    cond = {"type": "not", "children": [_cmp("extensions.status")]}
    assert _check_condition_fields(_payload(cond)) == []
