"""Regression tests for shared engine API payload schema contracts (#493)."""

from __future__ import annotations

from typing import Any

import pytest
from dap_engine.api.schemas import AgentDryRunDraft, AgentExportPayload, PipelineExportPayload
from dap_engine.contracts import AgentCreate, AgentUpdate, PipelineCreate, PipelineUpdate
from pydantic import BaseModel, ValidationError


def _agent_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Shared Schema Agent",
        "role": "test_author",
        "runtime_id": "api-call",
        "runtime_config": {},
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": ["available_issues"],
        "output_schema": ["test_files"],
        "constraints": [],
        "budget_limit_usd": None,
        "timeout_ms": 60_000,
    }
    payload.update(overrides)
    return payload


def _agent_update_payload(**overrides: Any) -> dict[str, Any]:
    payload = _agent_payload(**overrides)
    payload.pop("role")
    return payload


def _pipeline_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Shared Schema Pipeline",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": "agent-1"}],
        "edges": [{"id": "e1", "source": "__start__", "target": "n1"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
        "ui_metadata": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "model,payload",
    [
        (AgentCreate, _agent_payload(input_schema=["extensions."])),
        (AgentUpdate, _agent_update_payload(name=None, input_schema=["extensions."])),
        (AgentExportPayload, _agent_payload(input_schema=["extensions."])),
        (AgentDryRunDraft, _agent_payload(input_schema=["extensions."])),
    ],
)
def test_agent_payload_variants_share_field_schema_validation(
    model: type[BaseModel],
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="unsupported field"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", [AgentCreate, AgentUpdate, AgentExportPayload, AgentDryRunDraft])
def test_agent_payload_variants_share_extension_schema_support(
    model: type[BaseModel],
) -> None:
    payload = (
        _agent_update_payload(name=None, input_schema=["issue_number"])
        if model is AgentUpdate
        else _agent_payload(input_schema=["issue_number"])
    )

    parsed = model.model_validate(payload)

    assert parsed.model_dump()["input_schema"] == ["issue_number"]


@pytest.mark.parametrize("model", [AgentCreate, AgentUpdate, AgentExportPayload, AgentDryRunDraft])
def test_agent_payload_variants_share_legacy_schema_coercion(model: type[BaseModel]) -> None:
    payload = _agent_update_payload(name=None) if model is AgentUpdate else _agent_payload()

    parsed = model.model_validate(payload | {"input_schema": {}, "output_schema": {}})
    data = parsed.model_dump()

    assert data["input_schema"] == []
    assert data["output_schema"] == []


@pytest.mark.parametrize("model", [PipelineCreate, PipelineUpdate, PipelineExportPayload])
def test_pipeline_payload_variants_share_graph_field_validation(model: type[BaseModel]) -> None:
    payload = (
        _pipeline_payload(name=None, description=None, state_schema_ref="")
        if model is PipelineUpdate
        else _pipeline_payload(state_schema_ref="")
    )

    with pytest.raises(ValidationError, match="state_schema_ref"):
        model.model_validate(payload)
