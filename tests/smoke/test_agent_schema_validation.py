"""Type-level tests for ``Agent.input_schema`` / ``output_schema`` (#58).

The Pydantic ``Agent`` model owns:

- the legacy-dict → ``[]`` coercion (so existing DB rows survive the
  shape change without a migration), and
- the unknown-field rejection (so callers get a descriptive error
  instead of a silent ignore).

These tests run the model directly — the API layer reuses the same
helpers in ``apps/engine/.../api/schemas.py``, exercised in
``test_crud_agents.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from dap_types import Agent
from pydantic import ValidationError


def _agent_kwargs(**overrides: Any) -> dict[str, Any]:
    """Minimal keyword set for constructing an Agent — caller overrides what matters."""
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": "agent-1",
        "name": "Stub",
        "role": "test_author",
        "version": 1,
        "runtime_id": "api-call",
        "runtime_config": {},
        "prompt_template": "<agent_prompt/>",
        "input_schema": [],
        "output_schema": [],
        "constraints": [],
        "budget_limit_usd": None,
        "timeout_ms": 60_000,
        "created_at": now,
        "updated_at": now,
        "is_active": True,
    }
    base.update(overrides)
    return base


def test_legacy_empty_dict_coerces_to_empty_list() -> None:
    """Old DB rows with ``{}`` survive a model_validate read-back."""
    agent = Agent.model_validate(_agent_kwargs(input_schema={}, output_schema={}))
    assert agent.input_schema == []
    assert agent.output_schema == []


def test_legacy_non_empty_dict_coerces_to_empty_list() -> None:
    """Placeholder JSON-Schema blobs lose their (meaningless) shape but read back fine."""
    agent = Agent.model_validate(
        _agent_kwargs(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
        )
    )
    assert agent.input_schema == []
    assert agent.output_schema == []


def test_none_coerces_to_empty_list() -> None:
    agent = Agent.model_validate(_agent_kwargs(input_schema=None, output_schema=None))
    assert agent.input_schema == []
    assert agent.output_schema == []


def test_known_field_names_pass() -> None:
    agent = Agent.model_validate(
        _agent_kwargs(
            input_schema=["available_issues"],
            output_schema=["test_files", "tests_generated"],
        )
    )
    assert agent.input_schema == ["available_issues"]
    assert agent.output_schema == ["test_files", "tests_generated"]


def test_unknown_field_in_input_schema_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Agent.model_validate(_agent_kwargs(input_schema=["definitely_not_real"]))
    assert "definitely_not_real" in str(exc.value)


def test_unknown_field_in_output_schema_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Agent.model_validate(_agent_kwargs(output_schema=["bogus_field"]))
    assert "bogus_field" in str(exc.value)


def test_duplicate_field_names_raise() -> None:
    with pytest.raises(ValidationError) as exc:
        Agent.model_validate(_agent_kwargs(output_schema=["test_files", "test_files"]))
    assert "duplicate" in str(exc.value).lower()


def test_non_string_element_raises() -> None:
    """Pydantic should reject elements that aren't ``str`` outright."""
    with pytest.raises(ValidationError):
        Agent.model_validate(_agent_kwargs(input_schema=[42]))


def test_default_factory_yields_empty_lists() -> None:
    """Constructing without explicit schemas gives ``[]`` (the new default)."""
    kwargs = _agent_kwargs()
    kwargs.pop("input_schema")
    kwargs.pop("output_schema")
    agent = Agent.model_validate(kwargs)
    assert agent.input_schema == []
    assert agent.output_schema == []
