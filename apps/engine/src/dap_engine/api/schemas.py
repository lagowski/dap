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
    minus the fields the server filled in (id, version, timestamps,
    archived_at), keeping validators aligned with ``POST /agents``.
    """


class BundledAgentImportPayload(AgentNamedPayload):
    """Agent payload variant used inside pipeline-export bundles.

    Kept as a named API component so generated OpenAPI clients retain the
    existing bundle schema surface. Validation is intentionally inherited from
    ``AgentNamedPayload``: top-level PipelineState fields and extension field
    references use the same formalized rules as normal agent create/import.
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


# ``Final`` narrows the inferred type to ``Literal["pipeline-export/2"]``
# so the constant is assignable to the ``Literal`` schema_version
# fields below without mypy complaining. Same string, stronger type.
PIPELINE_EXPORT_SCHEMA_VERSION: Final = "pipeline-export/2"


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

    schema_version: Literal["pipeline-export/1", "pipeline-export/2"] = (
        PIPELINE_EXPORT_SCHEMA_VERSION
    )
    min_dap_version: str | None = None
    pipeline: PipelineExportPayload
    bundled_agents: dict[str, AgentExportPayload] | None = None


class PipelineImportRequest(BaseModel):
    """Body of ``POST /pipelines/import`` — same shape as :class:`PipelineExport`.

    ``schema_version`` accepts both the legacy v1 envelope and the
    Cortex/DAP v2 envelope.

    v2 optional fields:
    - ``min_dap_version``: semver gate — engine rejects bundles that
      require a newer DAP than the running instance.
    - ``_comment``: free-text documentation block, silently ignored.
    - ``install_instructions``: operator install guide, silently ignored.
    - ``backend_profiles``: per-provider agent assignment map,
      persisted on the pipeline version and returned on GET.
    - ``bundled_agents``: same as v1; agent ``input_schema`` and
      ``output_schema`` may reference top-level ``PipelineState`` fields
      or pipeline-defined extension fields that live in
      ``PipelineState.extensions``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["pipeline-export/1", "pipeline-export/2"]
    min_dap_version: str | None = None
    pipeline: PipelineExportPayload
    bundled_agents: dict[str, BundledAgentImportPayload] | None = None
    backend_profiles: dict[str, Any] | None = None
    # Ignored on import, but real bundles ship it as a structured block (operator
    # steps + required env vars), not just a string — accept any shape so a full
    # bundle doesn't 422 with a ``string_type`` error (#755).
    install_instructions: Any | None = None
    # ``_comment`` is a reserved Python name convention; accept it via alias.
    # Also accepted as any shape for the same reason.
    comment: Any | None = Field(default=None, alias="_comment")


class BackendProfileInspection(BaseModel):
    """Availability result for one backend profile declared by a bundle."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str | None = None
    requires_env: list[str] = Field(default_factory=list)
    missing_env: list[str] = Field(default_factory=list)
    requires_service: str | None = None
    service_available: bool | None = None
    available: bool


class BackendProfilesInspectionResponse(BaseModel):
    """Response for the bundle-import backend configuration step."""

    model_config = ConfigDict(extra="forbid")

    default_profile: str | None = None
    overrides: dict[str, str] = Field(default_factory=dict)
    profiles: list[BackendProfileInspection] = Field(default_factory=list)


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
    has_more: bool


class PaginatedPipelines(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int
    has_more: bool


class PaginatedRuns(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int
    has_more: bool
