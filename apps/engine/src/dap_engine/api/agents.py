"""REST CRUD for agents + prompt render preview + import/export (#94) + dry-run (#103)."""

from __future__ import annotations

import tempfile
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple

from dap_prompt_dsl import PromptBuildError, build_prompt
from dap_runtimes import RuntimeRegistry
from dap_types import Agent, RuntimeTask
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_engine_config, get_registry, get_session
from dap_engine.api.export_redaction import scrub_secret_like_keys
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.persistence.models import UserORM

if TYPE_CHECKING:
    # Runtime cycle: dap_engine.app imports this router module, so we
    # can't import EngineConfig at runtime. The type is needed only
    # for the dry_run endpoint signature; behaviour is unchanged.
    from dap_engine.app import EngineConfig
from dap_engine.api.schemas import (
    AGENT_EXPORT_SCHEMA_VERSION,
    AgentDryRunRequest,
    AgentDryRunResponse,
    AgentExport,
    AgentExportPayload,
    AgentImportRequest,
    OutputSchemaValidation,
    RenderPreviewRequest,
    RenderPreviewResponse,
)
from dap_engine.contracts import AgentCreate, AgentUpdate
from dap_engine.persistence import repository as repo
from dap_engine.runtime_policy import runtime_policy_error

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    role: str | None = Query(default=None),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_agents(
        session,
        actor_id=user.id,
        is_admin=user.is_superuser,
        role=role,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    usage = repo.count_pipelines_using_agents(session, [a.id for a in items])
    enriched = [a.model_copy(update={"used_in_pipelines": usage.get(a.id, 0)}) for a in items]
    return {
        "items": [a.model_dump(mode="json") for a in enriched],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Agent)
def create_agent(
    payload: AgentCreate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Agent:
    return repo.create_agent(session, payload, user_id=user.id)


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    response_model=Agent,
)
def import_agent(
    payload: AgentImportRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    user: UserORM = Depends(current_active_user),
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
    return repo.create_agent(session, create_payload, user_id=user.id)


@router.get("/{agent_id}", response_model=Agent)
def get_agent(
    agent_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Agent:
    try:
        return repo.get_agent(session, agent_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{agent_id}", response_model=Agent)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Agent:
    try:
        return repo.update_agent(
            session,
            agent_id,
            payload,
            actor_id=user.id,
            is_admin=user.is_superuser,
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_agent(
    agent_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Response:
    # Ownership gate first — otherwise the 409-precheck below leaks info
    # about foreign agents (id existence + names of blocking pipelines).
    # The cheapest gate is a get_agent() call: it returns the row only if
    # the caller owns it (or is admin) and raises NotFoundError otherwise,
    # which we surface as 404 — same response a non-existent id gets.
    try:
        repo.get_agent(session, agent_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    blocking = repo.pipelines_using_agent(session, agent_id)
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Agent is referenced by {len(blocking)} active pipeline"
                    f"{'s' if len(blocking) != 1 else ''}. Remove or replace the "
                    "node(s) before archiving."
                ),
                "blocking_pipelines": [{"id": pid, "name": pname} for pid, pname in blocking],
            },
        )
    try:
        repo.archive_agent(session, agent_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/versions", response_model=list[Agent])
def list_agent_versions(
    agent_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> list[Agent]:
    try:
        return repo.list_agent_versions(
            session, agent_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/versions/{version}", response_model=Agent)
def get_agent_version(
    agent_id: str,
    version: int,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Agent:
    try:
        return repo.get_agent_version(
            session, agent_id, version, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/export", response_model=AgentExport)
def export_agent(
    agent_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
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
        agent = repo.get_agent(session, agent_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return AgentExport(
        schema_version=AGENT_EXPORT_SCHEMA_VERSION,
        agent=build_agent_export_payload(agent),
    )


def build_agent_export_payload(agent: Agent) -> AgentExportPayload:
    """Project an :class:`Agent` ORM-backed model onto its portable shape.

    Strips per-installation fields (id, version, timestamps,
    archived_at) and scrubs ``runtime_config`` for credential-shaped
    keys (``api_key`` / ``token`` / ``secret`` / ``password`` /
    ``credential``). Used both by the standalone agent export
    endpoint and the pipeline bundle export (#126).

    Raises ``HTTPException(500)`` when the stored shape can't be
    re-validated against ``AgentExportPayload`` — a real defence
    against a future migration introducing data we can't export,
    not a routine error path.
    """
    try:
        return AgentExportPayload(
            name=agent.name,
            role=agent.role,
            runtime_id=agent.runtime_id,
            runtime_config=scrub_secret_like_keys(agent.runtime_config),
            prompt_template=agent.prompt_template,
            input_schema=list(agent.input_schema),
            output_schema=list(agent.output_schema),
            constraints=list(agent.constraints),
            budget_limit_usd=agent.budget_limit_usd,
            timeout_ms=agent.timeout_ms,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored agent could not be re-validated for export: {exc.errors()}",
        ) from exc


@router.post("/{agent_id}/render-preview", response_model=RenderPreviewResponse)
def render_preview(
    agent_id: str,
    payload: RenderPreviewRequest,
    version: int | None = Query(default=None, description="Agent version (default: current)"),
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> RenderPreviewResponse:
    """Render the agent's prompt_template with the supplied context.

    Returns the XML output even when invalid so callers can see the diagnostics.
    Returns 422 only for hard rendering errors (Jinja syntax / undefined vars).
    """
    try:
        agent = (
            repo.get_agent_version(
                session, agent_id, version, actor_id=user.id, is_admin=user.is_superuser
            )
            if version is not None
            else repo.get_agent(session, agent_id, actor_id=user.id, is_admin=user.is_superuser)
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        result = build_prompt(
            agent.prompt_template,
            payload.context,
            input_schema=agent.input_schema or None,
            agent_metadata={"role": agent.role},
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


class _DryRunSource(NamedTuple):
    """Resolved fields needed to execute one dry-run.

    Sourced from either a saved agent (``agent_id`` path) or an inline
    draft (``draft`` path). Bundled into a NamedTuple so adding a new
    field doesn't keep widening a positional return tuple at the call
    site.
    """

    runtime_id: str
    role: str
    prompt_template: str
    input_schema: list[str]
    output_schema: list[str]
    runtime_config: dict[str, Any]
    timeout_ms: int
    agent_budget_usd: float | None


def _resolve_dryrun_source(
    payload: AgentDryRunRequest,
    session: Session,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> _DryRunSource:
    """Resolve the agent fields needed for a dry-run.

    Fetches a saved agent or unwraps the inline draft; the schema
    validator on ``AgentDryRunRequest`` guarantees exactly one path
    is taken.
    """
    if payload.draft is not None:
        d = payload.draft
        return _DryRunSource(
            runtime_id=d.runtime_id,
            role=d.role,
            prompt_template=d.prompt_template,
            input_schema=list(d.input_schema),
            output_schema=list(d.output_schema),
            runtime_config=dict(d.runtime_config),
            timeout_ms=d.timeout_ms,
            agent_budget_usd=d.budget_limit_usd,
        )
    assert payload.agent_id is not None
    try:
        agent = (
            repo.get_agent_version(
                session,
                payload.agent_id,
                payload.agent_version,
                actor_id=actor_id,
                is_admin=is_admin,
            )
            if payload.agent_version is not None
            else repo.get_agent(session, payload.agent_id, actor_id=actor_id, is_admin=is_admin)
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _DryRunSource(
        runtime_id=agent.runtime_id,
        role=agent.role,
        prompt_template=agent.prompt_template,
        input_schema=list(agent.input_schema),
        output_schema=list(agent.output_schema),
        runtime_config=dict(agent.runtime_config),
        timeout_ms=agent.timeout_ms,
        agent_budget_usd=agent.budget_limit_usd,
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
    user: UserORM = Depends(current_active_user),
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
    source = _resolve_dryrun_source(payload, session, actor_id=user.id, is_admin=user.is_superuser)

    if not registry.has(source.runtime_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown runtime_id '{source.runtime_id}' — no adapter registered. "
                "Install the adapter or pick a different runtime."
            ),
        )
    denial = runtime_policy_error(
        source.runtime_id,
        is_admin=user.is_superuser,
        allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
    )
    if denial is not None:
        record_audit_event(
            session,
            user_id=user.id,
            event_type="runtime_policy.denied",
            event_data={
                "surface": "agents.dry_run",
                "runtime_id": source.runtime_id,
                "reason": denial,
            },
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=denial)

    # Budget cap. The lower of agent's own ``budget_limit_usd`` and the
    # engine-wide ``dry_run_budget_usd`` wins; if either declares a
    # value above the engine cap, refuse before invocation.
    cap = config.dry_run_budget_usd
    agent_budget = source.agent_budget_usd
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
            source.prompt_template,
            payload.context,
            input_schema=source.input_schema or None,
            agent_metadata={"role": source.role},
        )
    except PromptBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    adapter = registry.get(source.runtime_id)

    with tempfile.TemporaryDirectory(prefix="dap-dryrun-") as workdir:
        task = RuntimeTask(
            execution_id=f"dryrun-{uuid.uuid4().hex[:12]}",
            prompt_xml=build_result.xml,
            working_directory=workdir,
            timeout_ms=source.timeout_ms,
            budget_usd=effective_budget,
            runtime_config=source.runtime_config,
        )
        runtime_result = await adapter.execute(task)

    output_check = _validate_output_schema(runtime_result.structured, source.output_schema)

    return AgentDryRunResponse(
        rendered_xml=build_result.xml,
        prompt_warnings=list(build_result.warnings),
        prompt_errors=list(build_result.errors),
        runtime_result=runtime_result.model_dump(mode="json"),
        output_schema_validation=output_check,
    )
