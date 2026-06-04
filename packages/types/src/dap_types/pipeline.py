from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ComparisonOperator = Literal["==", "!=", "<", "<=", ">", ">="]


# Map cortex's short operator aliases to DAP's symbolic ones.
# Symbolic operators pass through unchanged.
_OPERATOR_ALIASES: dict[str, ComparisonOperator] = {
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}


def _normalize_legacy_condition(value: Any) -> Any:
    """Accept cortex's legacy edge-condition shape.

    DAP's strict shape (the canonical one validators expect):
      Comparison: {"type": "comparison", "field": ..., "operator": ..., "value": ...}
      Logical:    {"type": "and"|"or", "children": [<EdgeCondition>, ...]}

    Cortex's legacy shape (shipped via cortex bundles pre-2026-06 schema migration):
      Comparison: {"field": ..., "op": ..., "value": ...}
      Logical:    {"field": ..., "op": ..., "value": ...,
                   "and": {"field": ..., "op": ..., "value": ...}}
                  (the outer keys are the first conjunct; the nested "and" or "or" is the second)

    This normalizer detects the legacy shape (presence of "op" key without "type" key)
    and rewrites it to the strict shape that EdgeCondition's discriminated union accepts.

    Backward-compat path. Once cortex's exporter emits the strict shape on all edges,
    this normalizer becomes a no-op and can be removed.

    Tracked at #635.
    """
    if not isinstance(value, dict):
        return value

    # Already in strict shape (has the type discriminator) — no transformation needed.
    if "type" in value:
        return value

    # Legacy shape requires an "op" key on a comparison.
    if "op" not in value:
        return value

    op_raw = value["op"]
    operator = _OPERATOR_ALIASES.get(op_raw, op_raw)

    base_comparison = {
        "type": "comparison",
        "field": value.get("field", ""),
        "operator": operator,
        "value": value.get("value"),
    }

    # Detect legacy compound: nested "and" or "or" key on the same object.
    # The outer keys become the first conjunct; the nested becomes the second.
    for compound_type in ("and", "or"):
        nested = value.get(compound_type)
        if isinstance(nested, dict):
            return {
                "type": compound_type,
                "children": [
                    base_comparison,
                    _normalize_legacy_condition(nested),
                ],
            }
        if isinstance(nested, list):
            return {
                "type": compound_type,
                "children": [base_comparison] + [_normalize_legacy_condition(c) for c in nested],
            }

    # No compound — just a comparison.
    return base_comparison


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

    @field_validator("condition", mode="before")
    @classmethod
    def _accept_legacy_condition_shape(cls, v: Any) -> Any:
        """Accept cortex's legacy edge-condition format.

        See ``_normalize_legacy_condition`` docstring for the shape mapping.
        """
        return _normalize_legacy_condition(v)


class PipelineDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    budget_limit_usd: float = 5.0
    approval_required_nodes: list[str] = Field(default_factory=list)
    gate_timeout_seconds: int = Field(
        default=3600,
        gt=0,
        description=(
            "Seconds a gate waits for human approval before the run is "
            "marked failed with failure_reason='gate approval timed out'. "
            "Defaults to 1 hour. Set per-pipeline in defaults.gate_timeout_seconds."
        ),
    )
    requires_terminal_final_status: bool = Field(
        default=False,
        description=(
            "When true, the orchestrator treats a run that finishes with "
            "``final_status='running'`` as a failed run (#381). Cortex-style "
            "pipelines, where the last node decides success/failure "
            "explicitly (e.g. pr-merger refuses to merge), should opt in. "
            "Generic pipelines that don't manage state-level final_status "
            "leave this off and rely on the 'no node raised' = 'success' "
            "default."
        ),
    )


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
    ui_metadata: dict[str, Any] | None = None
    backend_profiles: dict[str, Any] | None = None


# Resolve forward reference for self-referential LogicalCondition.children
LogicalCondition.model_rebuild()
