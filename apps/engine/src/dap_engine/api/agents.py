"""REST CRUD for agents + prompt render preview + import/export (#94) + dry-run (#103)."""

from __future__ import annotations

import tempfile
import uuid
from typing import TYPE_CHECKING, Any

from dap_prompt_dsl import PromptBuildError, build_prompt
from dap_runtimes import RuntimeRegistry
from dap_types import Agent, RuntimeTask
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_engine_config, get_registry, get_session

if TYPE_CHECKING:
    # Runtime cycle: dap_engine.app imports this router module, so we
    # can't import EngineConfig at runtime. The type is needed only
    # for the dry_run endpoint signature; behaviour is unchanged.
    from dap_engine.app import EngineConfig
from dap_engine.api.schemas import (
    AGENT_EXPORT_SCHEMA_VERSION,
    AgentCreate,
    AgentDryRunRequest,
    AgentDryRunResponse,
    AgentExport,
    AgentExportPayload,
    AgentImportRequest,
    AgentUpdate,
    OutputSchemaValidation,
    RenderPreviewRequest,
    RenderPreviewResponse,
)
from dap_engine.persistence import repository as repo

router = APIRouter(prefix="/agents", tags=["agents"])


_SECRET_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
)
_REDACTED_PLACEHOLDER = "<redacted>"


def _scrub_secret_like_keys(value: dict[str, Any]) -> dict[str, Any]:
    """Best-effort redaction of keys whose name suggests a credential.

    Secrets are *supposed* to live in environment variables — adapters
    read API keys from ``os.environ``, not from ``runtime_config``. But
    nothing in the schema enforces that, so old or hand-edited agents
    may have a literal key sitting in ``runtime_config``. Exports are
    portable artifacts (checked into git, shared between machines), so
    we redact known-suspect keys before serialising.
    """
    redacted: dict[str, Any] = {}
    for key, val in value.items():
        if any(pattern in key.lower() for pattern in _SECRET_KEY_PATTERNS):
            redacted[key] = _REDACTED_PLACEHOLDER
        elif isinstance(val, dict):
            redacted[key] = _scrub_secret_like_keys(val)
        else:
            redacted[key] = val
    return redacted


@router.get("")
def list_agents(
    session: Session = Depends(get_session),
    role: str | None = Query(default=None),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_agents(
        session,
        role=role,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [a.model_dump(mode="json") for a in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Agent)
def create_agent(payload: AgentCreate, session: Session = Depends(get_session)) -> Agent:
    return repo.create_agent(session, payload)


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    response_model=Agent,
)
def import_agent(
    payload: AgentImportRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
) -> Agent:
    """Create a new agent (v1) from an exported JSON payload (#94).

    ``schema_version`` and field-list validators run during request
    parsing (Pydantic). The runtime_id check happens here because it
    needs the runtime registry. Everything else delegates to the
    standard create flow — same validation, same versioning, same
    response shape.
    """
    if not registry.has(payload.agent.runtime_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown runtime_id '{payload.agent.runtime_id}' — "
                "this engine has no adapter registered with that id."
            ),
        )

    # Round-trip through model_dump → model_validate so that any future
    # field added to AgentExportPayload/AgentCreate flows automatically,
    # rather than relying on this function to be edited in lockstep.
    try:
        create_payload = AgentCreate.model_validate(payload.agent.model_dump())
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc
    return repo.create_agent(session, create_payload)


@router.get("/{agent_id}", response_model=Agent)
def get_agent(agent_id: str, session: Session = Depends(get_session)) -> Agent:
    try:
        return repo.get_agent(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{agent_id}", response_model=Agent)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: Session = Depends(get_session),
) -> Agent:
    try:
        return repo.update_agent(session, agent_id, payload)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_agent(agent_id: str, session: Session = Depends(get_session)) -> Response:
    try:
        repo.archive_agent(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/versions", response_model=list[Agent])
def list_agent_versions(agent_id: str, session: Session = Depends(get_session)) -> list[Agent]:
    try:
        return repo.list_agent_versions(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/versions/{version}", response_model=Agent)
def get_agent_version(
    agent_id: str,
    version: int,
    session: Session = Depends(get_session),
) -> Agent:
    try:
        return repo.get_agent_version(session, agent_id, version)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/export", response_model=AgentExport)
def export_agent(
    agent_id: str,
    session: Session = Depends(get_session),
) -> AgentExport:
    """Return a portable JSON shape of the agent (#94).

    Strips per-installation fields (id, version, timestamps,
    archived_at) so the result can move between DAP installations
    or be checked into git. Secrets stay in env — adapters read API
    keys from process env, not from the exported JSON. As a
    belt-and-suspenders defence, ``runtime_config`` is also walked
    for keys that look like credentials (``api_key``, ``token``,
    ``secret``, ``password``, ``credential``) and their values are
    replaced with ``<redacted>`` before serialisation.
    """
    try:
        agent = repo.get_agent(session, agent_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        payload = AgentExportPayload(
            name=agent.name,
            role=agent.role,
            runtime_id=agent.runtime_id,
            runtime_config=_scrub_secret_like_keys(agent.runtime_config),
            prompt_template=agent.prompt_template,
            input_schema=list(agent.input_schema),
            output_schema=list(agent.output_schema),
            constraints=list(agent.constraints),
            budget_limit_usd=agent.budget_limit_usd,
            timeout_ms=agent.timeout_ms,
        )
    except ValidationError as exc:
        # Shouldn't happen — agent stored shape was already validated
        # on write — but if a future migration ever introduces a stored
        # shape we can't export, surface it as 500 with the details
        # rather than crashing without context.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored agent could not be re-validated for export: {exc.errors()}",
        ) from exc

    return AgentExport(schema_version=AGENT_EXPORT_SCHEMA_VERSION, agent=payload)


@router.post("/{agent_id}/render-preview", response_model=RenderPreviewResponse)
def render_preview(
    agent_id: str,
    payload: RenderPreviewRequest,
    version: int | None = Query(default=None, description="Agent version (default: current)"),
    session: Session = Depends(get_session),
) -> RenderPreviewResponse:
    """Render the agent's prompt_template with the supplied context.

    Returns the XML output even when invalid so callers can see the diagnostics.
    Returns 422 only for hard rendering errors (Jinja syntax / undefined vars).
    """
    try:
        agent = (
            repo.get_agent_version(session, agent_id, version)
            if version is not None
            else repo.get_agent(session, agent_id)
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        result = build_prompt(
            agent.prompt_template,
            payload.context,
            input_schema=agent.input_schema or None,
        )
    except PromptBuildError as exc:
        raise HTTPException(
            status_code=422,  # Unprocessable Content
            detail=str(exc),
        ) from exc

    return RenderPreviewResponse(
        rendered_xml=result.xml,
        valid=result.valid,
        warnings=result.warnings,
        errors=result.errors,
    )


# ---------------------------------------------------------------------------
# Dry-run (#103) — execute the agent end-to-end against sample context
# without persisting anything. The Test panel on the agent create/edit
# pages is the primary caller.
# ---------------------------------------------------------------------------


def _resolve_dryrun_source(
    payload: AgentDryRunRequest, session: Session
) -> tuple[str, str, list[str], list[str], dict[str, Any], int, float | None]:
    """Return (runtime_id, prompt_template, input_schema, output_schema,
    runtime_config, timeout_ms, agent_budget_usd) for the request, fetching
    a saved agent or unwrapping the inline draft."""
    if payload.draft is not None:
        d = payload.draft
        return (
            d.runtime_id,
            d.prompt_template,
            list(d.input_schema),
            list(d.output_schema),
            dict(d.runtime_config),
            d.timeout_ms,
            d.budget_limit_usd,
        )
    # agent_id path — schema validator guarantees exactly one of the
    # two is set, so this branch only runs when agent_id is present.
    assert payload.agent_id is not None
    try:
        agent = (
            repo.get_agent_version(session, payload.agent_id, payload.agent_version)
            if payload.agent_version is not None
            else repo.get_agent(session, payload.agent_id)
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return (
        agent.runtime_id,
        agent.prompt_template,
        list(agent.input_schema),
        list(agent.output_schema),
        dict(agent.runtime_config),
        agent.timeout_ms,
        agent.budget_limit_usd,
    )


def _validate_output_schema(
    structured: dict[str, Any] | None, expected: list[str]
) -> OutputSchemaValidation:
    """Soft-check the runtime's structured output against ``output_schema``.

    No declared schema → ``checked=False`` (we don't pretend to validate).
    No structured output (CLI returned plain text) → ``checked=False`` with
    a note so the panel can explain why nothing was checked.
    """
    if not expected:
        return OutputSchemaValidation(
            valid=True,
            checked=False,
            note="output_schema is empty — nothing to validate against",
        )
    if structured is None:
        return OutputSchemaValidation(
            valid=False,
            checked=False,
            missing_fields=list(expected),
            note="runtime did not return a structured payload — output schema cannot be checked",
        )
    expected_set = set(expected)
    actual_set = set(structured.keys())
    missing = sorted(expected_set - actual_set)
    extras = sorted(actual_set - expected_set)
    return OutputSchemaValidation(
        valid=not missing,
        checked=True,
        missing_fields=missing,
        extra_fields=extras,
    )


@router.post("/dry-run", response_model=AgentDryRunResponse)
async def dry_run_agent(
    payload: AgentDryRunRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    config: EngineConfig = Depends(get_engine_config),
) -> AgentDryRunResponse:
    """Execute one agent end-to-end without persisting anything (#103).

    Runs in a fresh ``tempfile.TemporaryDirectory`` so CLI runtimes that
    edit files (claude-code, aider, codex, bash) do their work in a
    sandbox that's discarded on response. No ``Run`` row, no
    ``NodeExecutionLog`` row — the result is returned only to the
    caller.

    Auth handled by the runtime adapter itself (env key OR stored OAuth
    session — see #99).
    """
    (
        runtime_id,
        prompt_template,
        input_schema,
        output_schema,
        runtime_config,
        timeout_ms,
        agent_budget,
    ) = _resolve_dryrun_source(payload, session)

    if not registry.has(runtime_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown runtime_id '{runtime_id}' — no adapter registered. "
                "Install the adapter or pick a different runtime."
            ),
        )

    # Budget cap. The lower of agent's own ``budget_limit_usd`` and the
    # engine-wide ``dry_run_budget_usd`` wins; if either declares a
    # value above the engine cap, refuse before invocation.
    cap = config.dry_run_budget_usd
    if agent_budget is not None and agent_budget > cap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Agent budget_limit_usd ({agent_budget:.4f}) exceeds the "
                f"dry-run cap ({cap:.4f}). Lower the agent's budget or raise "
                "DAP_DRY_RUN_BUDGET_USD on the engine."
            ),
        )
    effective_budget = min(agent_budget, cap) if agent_budget is not None else cap

    try:
        build_result = build_prompt(
            prompt_template,
            payload.context,
            input_schema=input_schema or None,
        )
    except PromptBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    adapter = registry.get(runtime_id)

    with tempfile.TemporaryDirectory(prefix="dap-dryrun-") as workdir:
        task = RuntimeTask(
            execution_id=f"dryrun-{uuid.uuid4().hex[:12]}",
            prompt_xml=build_result.xml,
            working_directory=workdir,
            timeout_ms=timeout_ms,
            budget_usd=effective_budget,
            runtime_config=runtime_config,
        )
        runtime_result = await adapter.execute(task)

    output_check = _validate_output_schema(runtime_result.structured, output_schema)

    return AgentDryRunResponse(
        rendered_xml=build_result.xml,
        prompt_warnings=list(build_result.warnings),
        prompt_errors=list(build_result.errors),
        runtime_result=runtime_result.model_dump(mode="json"),
        output_schema_validation=output_check,
    )
