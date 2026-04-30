"""Request schemas for engine REST API.

Response models reuse `dap_types.{Agent, Pipeline, Run, ...}` directly.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from dap_types.agent import coerce_legacy_field_list, validate_field_list
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)


AGENT_EXPORT_SCHEMA_VERSION = "agent-export/1"


class AgentExportPayload(BaseModel):
    """The portable subset of an agent — no per-installation fields.

    Mirrors :class:`AgentCreate` field-by-field minus the fields the
    server fills in (id, version, timestamps, archived_at). Reuses the
    same ``input_schema`` / ``output_schema`` validators so an
    exported agent that's importable here is also creatable directly
    via ``POST /agents``.
    """

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
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)


class AgentExport(BaseModel):
    """Response shape of ``GET /agents/{id}/export``.

    Versioned so future format changes can be detected at import time
    rather than silently producing odd state.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = AGENT_EXPORT_SCHEMA_VERSION
    agent: AgentExportPayload


class AgentImportRequest(BaseModel):
    """Body of ``POST /agents/import`` — same shape as :class:`AgentExport`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    agent: AgentExportPayload

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        if value != AGENT_EXPORT_SCHEMA_VERSION:
            msg = (
                f"Unsupported schema_version '{value}'. This engine accepts "
                f"'{AGENT_EXPORT_SCHEMA_VERSION}'."
            )
            raise ValueError(msg)
        return value


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
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)


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

    @field_validator("pipelines")
    @classmethod
    def _check_pipeline_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_pipeline_bindings_dict(value)


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


PIPELINE_EXPORT_SCHEMA_VERSION = "pipeline-export/1"


class PipelineExportPayload(BaseModel):
    """The portable subset of a pipeline — no per-installation fields.

    Mirrors :class:`PipelineCreate` field-by-field minus the fields
    the server fills in (``id``, ``version``, timestamps,
    ``archived_at``). Reuses the same node / edge / defaults shapes
    so an exported pipeline that's importable here is also creatable
    directly via ``POST /pipelines``.

    ``node.agent_id`` references the *source installation's* agent
    ids. The importer doesn't rewrite them — the existing DAG
    validator (#120) returns 422 if any referenced agent is missing
    or archived in the target DB. Bundling the referenced agents
    into the export envelope (so import auto-creates them and
    rewrites the references) is a separate follow-up.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = ""

    schema_version: Literal["langgraph/1.0"] = "langgraph/1.0"
    state_schema_ref: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)


class PipelineExport(BaseModel):
    """Response shape of ``GET /pipelines/{id}/export``.

    Versioned so future format changes can be detected at import
    time rather than silently producing odd state.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = PIPELINE_EXPORT_SCHEMA_VERSION
    pipeline: PipelineExportPayload


class PipelineImportRequest(BaseModel):
    """Body of ``POST /pipelines/import`` — same shape as :class:`PipelineExport`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    pipeline: PipelineExportPayload

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        if value != PIPELINE_EXPORT_SCHEMA_VERSION:
            msg = (
                f"Unsupported schema_version '{value}'. This engine accepts "
                f"'{PIPELINE_EXPORT_SCHEMA_VERSION}'."
            )
            raise ValueError(msg)
        return value


class ProjectRunRequest(BaseModel):
    """POST /projects/{project_id}/run/{kind} body — convenience trigger.

    All fields optional: the project resolves the bound pipeline_id
    from ``kind`` and seeds ``repo`` / ``branch`` defaults; the caller
    only supplies what they want to override per-call.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_version: int | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)


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


# ---------------------------------------------------------------------------
# Agent dry-run (#103) — execute one agent against sample context without
# creating an Agent version, a Run row, or a NodeExecutionLog. Used by the
# agent create/edit Test panel to validate prompt + runtime behaviour
# before saving.
# ---------------------------------------------------------------------------


class AgentDryRunDraft(BaseModel):
    """Inline agent definition for dry-runs from an unsaved form (``/agents/new``).

    Mirrors :class:`AgentCreate` field-by-field — same constraints, same
    field validators — so a draft that dry-runs cleanly is also creatable
    via ``POST /agents``. We don't reuse ``AgentCreate`` directly only to
    keep the type independent of any future create-only fields.
    """

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
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)


class AgentDryRunRequest(BaseModel):
    """Body of ``POST /agents/dry-run``.

    Exactly one of ``agent_id`` (saved) or ``draft`` (unsaved form) must
    be set. ``context`` is the sample state the prompt template renders
    against — same projection rule as a real run: when ``input_schema``
    is non-empty, keys outside it are dropped before render so the
    template only sees declared inputs; when ``input_schema`` is empty
    (legacy / contract-less agents), the context is passed through
    unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None
    agent_version: int | None = None
    draft: AgentDryRunDraft | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> Self:
        if (self.agent_id is None) == (self.draft is None):
            msg = "exactly one of 'agent_id' or 'draft' must be set (got both or neither)"
            raise ValueError(msg)
        return self


class OutputSchemaValidation(BaseModel):
    """Soft check of the runtime's structured output against ``output_schema``.

    Not fatal — a missing field is a warning, not a 422. The point is to
    surface it in the Test panel so the operator can adjust either the
    prompt or the schema.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool
    checked: bool
    missing_fields: list[str] = Field(default_factory=list)
    extra_fields: list[str] = Field(default_factory=list)
    note: str | None = None


class AgentDryRunResponse(BaseModel):
    """Response from ``POST /agents/dry-run``."""

    model_config = ConfigDict(extra="forbid")

    rendered_xml: str
    prompt_warnings: list[str] = Field(default_factory=list)
    prompt_errors: list[str] = Field(default_factory=list)
    runtime_result: dict[str, Any]
    output_schema_validation: OutputSchemaValidation


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
