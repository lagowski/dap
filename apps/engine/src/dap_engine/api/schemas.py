"""Request schemas for engine REST API.

Response models reuse `dap_types.{Agent, Pipeline, Run, ...}` directly.
"""

from __future__ import annotations

from typing import Any, Literal

from dap_types.agent import _coerce_field_list, _validate_field_list
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentCreate(BaseModel):
    """POST /agents body — server generates id, version=1, timestamps."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)

    runtime_id: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str

    input_schema: list[str] = Field(default_factory=list)
    output_schema: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = Field(default=60_000, gt=0)

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _coerce_legacy_dict_schema(cls, value: Any) -> list[str]:
        return _coerce_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return _validate_field_list(value)


class AgentUpdate(BaseModel):
    """PUT /agents/{id} body — creates a new immutable version."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)

    runtime_id: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str

    input_schema: list[str] = Field(default_factory=list)
    output_schema: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = Field(default=60_000, gt=0)

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _coerce_legacy_dict_schema(cls, value: Any) -> list[str]:
        return _coerce_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return _validate_field_list(value)


class PipelineCreate(BaseModel):
    """POST /pipelines body."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = ""

    schema_version: Literal["langgraph/1.0"] = "langgraph/1.0"
    state_schema_ref: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)


class PipelineUpdate(BaseModel):
    """PUT /pipelines/{id} body — creates a new version."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    schema_version: Literal["langgraph/1.0"] = "langgraph/1.0"
    state_schema_ref: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)


class RunCreateRequest(BaseModel):
    """POST /runs body — trigger pipeline execution.

    `initial_state` is a partial PipelineState dict; missing fields use defaults.
    `pipeline_version` is optional — defaults to current_version when omitted.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(min_length=1)
    pipeline_version: int | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)


class RenderPreviewRequest(BaseModel):
    """Body for POST /agents/{id}/render-preview."""

    model_config = ConfigDict(extra="forbid")

    context: dict[str, Any] = Field(default_factory=dict)


class RenderPreviewResponse(BaseModel):
    """Response from render-preview — XML output + validation outcome."""

    model_config = ConfigDict(extra="forbid")

    rendered_xml: str
    valid: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PaginatedAgents(BaseModel):
    items: list[Any]  # dap_types.Agent — Any to avoid circular import in this layer
    total: int
    offset: int
    limit: int


class PaginatedPipelines(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int


class PaginatedRuns(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int
