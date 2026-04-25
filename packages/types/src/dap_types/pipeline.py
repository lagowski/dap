from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ComparisonOperator = Literal["==", "!=", "<", "<=", ">", ">="]


class ComparisonCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["comparison"] = "comparison"
    field: str
    operator: ComparisonOperator
    value: str | int | float | bool | None


class LogicalCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["and", "or"]
    children: list[EdgeCondition]


EdgeCondition = Annotated[
    ComparisonCondition | LogicalCondition,
    Field(discriminator="type"),
]


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = 0
    y: float = 0


class NodeOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_config: dict[str, Any] | None = None
    budget_limit_usd: float | None = None
    timeout_ms: int | None = None


class PipelineNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    agent_id: str
    position: NodePosition = Field(default_factory=NodePosition)
    overrides: NodeOverrides | None = None


class PipelineEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    condition: EdgeCondition | None = None
    label: str | None = None


class PipelineDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    budget_limit_usd: float = 5.0
    approval_required_nodes: list[str] = Field(default_factory=list)


class Pipeline(BaseModel):
    """LangGraph-compatible DAG definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    version: int
    schema_version: Literal["langgraph/1.0"] = "langgraph/1.0"

    state_schema_ref: str
    entry_point: str

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]

    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)

    created_at: datetime
    updated_at: datetime
    is_active: bool = True


# Resolve forward reference for self-referential LogicalCondition.children
LogicalCondition.model_rebuild()
