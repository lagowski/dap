"""Request schemas for engine REST API.

Response models reuse `dap_types.{Agent, Pipeline, Run, ...}` directly.
"""

from __future__ import annotations

from typing import Any, Literal

from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    """POST /agents body — server generates id, version=1, timestamps."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)

    runtime_id: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = Field(default=60_000, gt=0)


class AgentUpdate(BaseModel):
    """PUT /agents/{id} body — creates a new immutable version."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)

    runtime_id: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = Field(default=60_000, gt=0)


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
