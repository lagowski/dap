"""API-only response and ancillary request types for the engine REST API.

Cross-layer request contracts (the bodies of POST/PUT mutations that
persistence and execution layers consume) live in
:mod:`dap_engine.contracts`. This module contains the rest: response
shapes, exports, dry-run payloads, pagination wrappers — anything that
should never be imported outside ``api/``.
"""

from __future__ import annotations

from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dap_engine.contracts import AgentNamedPayload, PipelineCreate

AGENT_EXPORT_SCHEMA_VERSION = "agent-export/1"


class AgentExportPayload(AgentNamedPayload):
    """The portable subset of an agent — no per-installation fields.

    Inherits the shared create/import/dry-run agent payload contract
    minus the fields the server fills in (id, version, timestamps,
    archived_at), keeping validators aligned with ``POST /agents``.
    """


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


# ``Final`` narrows the inferred type to ``Literal["pipeline-export/1"]``
# so the constant is assignable to the ``Literal`` schema_version
# fields below without mypy complaining. Same string, stronger type.
PIPELINE_EXPORT_SCHEMA_VERSION: Final = "pipeline-export/1"


class PipelineExportPayload(PipelineCreate):
    """The portable subset of a pipeline — no per-installation fields.

    Inherits the ``PipelineCreate`` payload contract minus the fields
    the server fills in (``id``, ``version``, timestamps,
    ``archived_at``), so node / edge / defaults shapes stay aligned
    between create, export, and import.

    ``node.agent_id`` references the *source installation's* agent
    ids. The importer doesn't rewrite them — the existing DAG
    validator (#120) returns 422 if any referenced agent is missing
    or archived in the target DB. Bundling the referenced agents
    into the export envelope (so import auto-creates them and
    rewrites the references) is a separate follow-up.
    """


class PipelineExport(BaseModel):
    """Response shape of ``GET /pipelines/{id}/export``.

    Versioned so future format changes can be detected at import
    time rather than silently producing odd state. The ``Literal``
    type locks the response contract — clients reading the OpenAPI
    schema see a single accepted value, and constructing a
    ``PipelineExport`` instance with anything else fails Pydantic
    validation at the call site.

    ``bundled_agents`` (#126) ships the agents this pipeline
    references so a target installation that doesn't have them yet
    can import the whole thing in one shot. Keys are the *source*
    installation's agent ids — the importer rewrites every
    ``node.agent_id`` in the pipeline payload to the freshly
    assigned local id before the DAG validator runs. ``None``
    means the export is pipeline-only (default behaviour, backward
    compat with Phase 1 exports).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pipeline-export/1"] = PIPELINE_EXPORT_SCHEMA_VERSION
    pipeline: PipelineExportPayload
    bundled_agents: dict[str, AgentExportPayload] | None = None


class PipelineImportRequest(BaseModel):
    """Body of ``POST /pipelines/import`` — same shape as :class:`PipelineExport`.

    Pydantic enforces ``schema_version`` against the literal — a
    different value comes back as a 422 from FastAPI's standard
    request-validation error path with the offending value visible
    in the detail. No custom validator needed.

    ``bundled_agents`` mirrors :class:`PipelineExport` — when
    present the importer creates each agent, builds an
    ``old_id → new_id`` map, and rewrites ``node.agent_id`` before
    persisting the pipeline. Missing field = legacy pipeline-only
    import (referenced agents must already exist locally).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pipeline-export/1"]
    pipeline: PipelineExportPayload
    bundled_agents: dict[str, AgentExportPayload] | None = None


class ProjectRunRequest(BaseModel):
    """POST /projects/{project_id}/run/{kind} body — convenience trigger.

    All fields optional: the project resolves the bound pipeline_id
    from ``kind`` and seeds ``repo`` / ``branch`` defaults; the caller
    only supplies what they want to override per-call.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_version: int | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)


class ValidateEnvRequest(BaseModel):
    """Body for POST /projects/validate-env — identify and probe GH tokens."""

    model_config = ConfigDict(extra="forbid")

    env_vars: dict[str, str]


class EnvVarValidationResult(BaseModel):
    """Per-key validation outcome."""

    model_config = ConfigDict(extra="forbid")

    key: str
    is_token: bool
    valid: bool | None = None
    login: str | None = None
    error: str | None = None


class ValidateEnvResponse(BaseModel):
    """Response from POST /projects/validate-env."""

    model_config = ConfigDict(extra="forbid")

    results: list[EnvVarValidationResult]


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


class AgentDryRunDraft(AgentNamedPayload):
    """Inline agent definition for dry-runs from an unsaved form (``/agents/new``).

    Inherits the shared create/import/dry-run agent payload contract,
    so a draft that dry-runs cleanly is also creatable via
    ``POST /agents``. Kept as a separate public schema name to preserve
    the OpenAPI component for clients.
    """


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
