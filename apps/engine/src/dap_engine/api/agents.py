"""REST CRUD for agents + prompt render preview + import/export (#94)."""

from __future__ import annotations

from typing import Any

from dap_prompt_dsl import PromptBuildError, build_prompt
from dap_runtimes import RuntimeRegistry
from dap_types import Agent
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_registry, get_session
from dap_engine.api.schemas import (
    AGENT_EXPORT_SCHEMA_VERSION,
    AgentCreate,
    AgentExport,
    AgentExportPayload,
    AgentImportRequest,
    AgentUpdate,
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
