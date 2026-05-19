"""Cross-layer request contracts (#252).

Persistence and execution layers consume these models when accepting
mutations from the API; keeping them at the package level instead of
under ``api/`` prevents the layer-inversion smell of
``persistence -> api`` and ``execution -> api`` imports.

Strictly request-side: response shapes, exports, dry-run payloads,
pagination wrappers, and other API-only types live in
:mod:`dap_engine.api.schemas`.
"""

from __future__ import annotations

from typing import Any, Literal

from dap_types.agent import coerce_legacy_field_list, validate_field_list
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_pipeline_bindings_dict(value: dict[str, str]) -> dict[str, str]:
    """Reject blank workflow kinds and blank pipeline ids in a binding dict.

    Empty/whitespace strings on either side of the binding lead to
    invalid project state that fails later when triggering a run —
    fail fast at request time with a clear 422 instead.
    """
    blank_kinds = sorted({k for k in value if not k.strip()})
    if blank_kinds:
        msg = (
            "pipelines: workflow kind keys must be non-blank "
            f"(got {len(blank_kinds)} blank entries)"
        )
        raise ValueError(msg)
    blank_ids = sorted({k for k, v in value.items() if not v.strip()})
    if blank_ids:
        msg = (
            "pipelines: pipeline ids must be non-blank — blank values "
            f"for kind(s): {', '.join(blank_ids)}"
        )
        raise ValueError(msg)
    return value


class AgentRuntimePayload(BaseModel):
    """Shared request/export fields for an agent version payload."""

    model_config = ConfigDict(extra="forbid")

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
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)


class AgentNamedPayload(AgentRuntimePayload):
    """Agent payload with the immutable role supplied by create/import/dry-run."""

    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)


class AgentCreate(AgentNamedPayload):
    """POST /agents body — server generates id, version=1, timestamps."""


class AgentUpdate(AgentRuntimePayload):
    """PUT /agents/{id} body — creates a new immutable version."""

    name: str | None = Field(default=None, min_length=1, max_length=200)


class PipelineGraphPayload(BaseModel):
    """Shared graph/defaults fields for pipeline create/update/export payloads."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["langgraph/1.0"] = "langgraph/1.0"
    state_schema_ref: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)
    ui_metadata: dict[str, Any] | None = None
    backend_profiles: dict[str, Any] | None = None


class PipelineCreate(PipelineGraphPayload):
    """POST /pipelines body."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class PipelineUpdate(PipelineGraphPayload):
    """PUT /pipelines/{id} body — creates a new version."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectCreate(BaseModel):
    """POST /projects body — server generates id + timestamps.

    ``pipelines`` values must reference existing non-archived pipelines;
    repository validates and raises ``ValueError`` (mapped to 422) if not.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = ""

    working_directory: str | None = None
    repo_url: str | None = None
    default_branch: str = Field(default="main", min_length=1, max_length=200)

    pipelines: dict[str, str] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    auto_approve_nodes: list[str] = Field(default_factory=list)

    @field_validator("pipelines")
    @classmethod
    def _check_pipeline_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_pipeline_bindings_dict(value)


class ProjectUpdate(BaseModel):
    """PUT /projects/{id} body — full replacement (no versioning for projects).

    Same shape as ``ProjectCreate`` minus the auto fields.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = ""

    working_directory: str | None = None
    repo_url: str | None = None
    default_branch: str = Field(default="main", min_length=1, max_length=200)

    pipelines: dict[str, str] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    auto_approve_nodes: list[str] = Field(default_factory=list)

    @field_validator("pipelines")
    @classmethod
    def _check_pipeline_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_pipeline_bindings_dict(value)


class RunCreateRequest(BaseModel):
    """POST /runs body — trigger pipeline execution.

    `initial_state` is a partial PipelineState dict; missing fields use defaults.
    `pipeline_version` is optional — defaults to current_version when omitted.
    `project_id` is optional (#64) — when set, the engine validates the
    project exists and stamps it on ``Run.project_id``. ``None`` keeps
    the run ad-hoc, matching pre-v0.6 behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(min_length=1)
    pipeline_version: int | None = None
    project_id: str | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)


class BatchRunCreateRequest(BaseModel):
    """POST /runs/batch body — trigger sequential execution of multiple issues."""

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(min_length=1)
    pipeline_version: int | None = None
    project_id: str | None = None
    issue_numbers: list[int] = Field(min_length=1)
    stop_on_failure: bool = True
    auto_approve: bool = False
