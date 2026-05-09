"""REST CRUD for pipelines."""

from __future__ import annotations

import logging
from typing import Any

from dap_types import Pipeline
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from dap_engine.api.agents import build_agent_export_payload
from dap_engine.api.deps import get_session
from dap_engine.api.schemas import (
    PIPELINE_EXPORT_SCHEMA_VERSION,
    AgentExportPayload,
    PipelineExport,
    PipelineExportPayload,
    PipelineImportRequest,
)
from dap_engine.contracts import AgentCreate, PipelineCreate, PipelineUpdate
from dap_engine.execution import ValidationResult, validate_pipeline_dag
from dap_engine.persistence import repository as repo

logger = logging.getLogger("dap.engine.pipelines")

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("")
def list_pipelines(
    session: Session = Depends(get_session),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_pipelines(
        session,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [p.model_dump(mode="json") for p in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _enforce_validation(payload: PipelineCreate, session: Session) -> ValidationResult:
    """Run the DAG validator and raise 422 on any errors.

    Used by ``create_pipeline`` and ``update_pipeline`` so a structurally
    broken or cohesion-incomplete pipeline can never land in the DB.
    The standalone ``POST /pipelines/validate`` endpoint stays intact —
    it's still useful for live feedback in the Pipeline Designer
    *before* the user clicks Save.

    Warnings are not fatal — they're operator hints (unused outputs,
    conflicting writers, etc.) and get logged here so operators see
    them in engine output. They also ride along on the 422 ``detail``
    when validation does fail, in case a related warning makes the
    error easier to interpret.
    """
    result = validate_pipeline_dag(payload, session)
    if result.warnings:
        logger.warning(
            "pipeline validation warnings: %s",
            "; ".join(result.warnings),
        )
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "errors": result.errors,
                "warnings": result.warnings,
            },
        )
    return result


def _update_to_create_shape(payload: PipelineUpdate) -> PipelineCreate:
    """Adapt a ``PipelineUpdate`` into a ``PipelineCreate`` for the validator.

    The DAG validator only inspects ``entry_point`` / ``nodes`` /
    ``edges`` / ``defaults`` — fields shared between the two types.
    Update-specific nullables (``name``, ``description``) get
    placeholders that satisfy the Create model's ``min_length=1``
    constraint without leaking into the actual DB write (the repo
    handles the update via the original ``PipelineUpdate``).
    """
    return PipelineCreate(
        name=payload.name or "(unchanged)",
        description=payload.description or "",
        schema_version=payload.schema_version,
        state_schema_ref=payload.state_schema_ref,
        entry_point=payload.entry_point,
        nodes=payload.nodes,
        edges=payload.edges,
        defaults=payload.defaults,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Pipeline)
def create_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
) -> Pipeline:
    """Create a pipeline (v1). Rejects invalid DAGs with 422 (#120).

    Validation happens *before* the DB write so a broken pipeline
    can never become persistent. The same checks the Pipeline
    Designer surfaces interactively are now enforced server-side.
    """
    _enforce_validation(payload, session)
    return repo.create_pipeline(session, payload)


@router.post("/validate", response_model=ValidationResult)
def validate_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
) -> ValidationResult:
    """Pre-save DAG validation — used by Pipeline Designer before submitting.

    Returns 200 with `valid: false` and a list of errors when invalid;
    422 only on Pydantic-level errors (malformed body). The save
    endpoints (``POST`` / ``PUT``) call the same validator and turn
    any errors into 422 — this endpoint stays as the live-feedback
    surface so the Designer can show diagnostics without committing.
    """
    return validate_pipeline_dag(payload, session)


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    response_model=Pipeline,
)
def import_pipeline(
    payload: PipelineImportRequest,
    session: Session = Depends(get_session),
) -> Pipeline:
    """Create a new pipeline (v1) from an exported JSON payload (#124).

    Two paths share this endpoint:

    - **Pipeline-only** (``bundled_agents`` absent): same as Phase 1.
      Referenced agents must already exist in this DB or the DAG
      validator returns 422.
    - **Bundle** (``bundled_agents`` present, #126): creates each
      bundled agent first, builds an ``old_id → new_id`` remap,
      rewrites every ``node.agent_id`` in the pipeline payload,
      then runs the standard validator + create path. The whole
      thing rides the request session, so a failure anywhere
      (agent validation, pipeline validation, repo write) rolls
      back the agents that were created earlier — no orphans
      land in the DB.

    Round-trips agents/pipeline through their ``Create`` types so
    any future field added flows automatically rather than relying
    on this function staying in lockstep with three models.
    """
    bundled = payload.bundled_agents
    pipeline_payload = payload.pipeline

    # Bundle mode is keyed on *presence* of the field, not truthiness
    # — the contract is "bundle if the caller chose to send it",
    # which an empty dict still expresses (no agents to remap, but
    # the user explicitly opted in). Keeps the contract documented
    # in :class:`PipelineImportRequest` accurate.
    if bundled is not None:
        # Step 1 — create bundled agents (no inter-agent ordering
        # required today). Build the remap from source ids to
        # fresh local ids.
        id_remap: dict[str, str] = {}
        for source_id, agent_payload in bundled.items():
            try:
                agent_create = AgentCreate.model_validate(agent_payload.model_dump())
            except ValidationError as exc:
                # Keep the structured Pydantic error list intact
                # under ``agent_validation_errors`` so frontends can
                # surface field-level issues. ``errors`` keeps the
                # human-friendly summary the rest of the import
                # paths use, including the offending source id so
                # operators know which entry in the bundle broke.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "errors": [
                            f"bundled_agents['{source_id}'] is not a valid agent payload",
                        ],
                        "warnings": [],
                        "agent_validation_errors": exc.errors(),
                        "source_id": source_id,
                    },
                ) from exc
            new_agent = repo.create_agent(session, agent_create)
            id_remap[source_id] = new_agent.id

        # Step 2 — rewrite node.agent_id in the pipeline payload.
        # Nodes whose agent_id isn't in the bundle keep their
        # original string; the validator will catch them if they
        # don't exist locally either (legitimate use case: a
        # partial bundle that relies on some pre-existing agents).
        rewritten_nodes = [
            node.model_copy(
                update={"agent_id": id_remap.get(node.agent_id, node.agent_id)},
            )
            for node in pipeline_payload.nodes
        ]
        pipeline_payload = pipeline_payload.model_copy(update={"nodes": rewritten_nodes})

    try:
        create_payload = PipelineCreate.model_validate(pipeline_payload.model_dump())
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    # ``_enforce_validation`` raises 422 on any DAG / cohesion
    # error. The session-wide rollback in ``get_session`` then
    # tears down the bundled agents created above — no orphans.
    _enforce_validation(create_payload, session)
    return repo.create_pipeline(session, create_payload)


@router.get("/{pipeline_id}", response_model=Pipeline)
def get_pipeline(pipeline_id: str, session: Session = Depends(get_session)) -> Pipeline:
    try:
        return repo.get_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{pipeline_id}", response_model=Pipeline)
def update_pipeline(
    pipeline_id: str,
    payload: PipelineUpdate,
    session: Session = Depends(get_session),
) -> Pipeline:
    """Update a pipeline (creates a new version). Rejects invalid DAGs with 422 (#120).

    Same enforcement as ``POST /pipelines`` — a failed update never
    rolls a new version. Existing pipeline keeps its current version
    when validation fails.

    Existence is checked **before** the validator: an unknown
    ``pipeline_id`` should always return 404 regardless of whether
    the body would also fail DAG validation. Otherwise a typo in
    the URL plus a typo in the body would surface as 422 and hide
    the real "this id doesn't exist" cause.
    """
    try:
        repo.get_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _enforce_validation(_update_to_create_shape(payload), session)
    return repo.update_pipeline(session, pipeline_id, payload)


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_session),
) -> Response:
    try:
        repo.archive_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{pipeline_id}/versions", response_model=list[Pipeline])
def list_pipeline_versions(
    pipeline_id: str,
    session: Session = Depends(get_session),
) -> list[Pipeline]:
    try:
        return repo.list_pipeline_versions(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{pipeline_id}/versions/{version}", response_model=Pipeline)
def get_pipeline_version(
    pipeline_id: str,
    version: int,
    session: Session = Depends(get_session),
) -> Pipeline:
    try:
        return repo.get_pipeline_version(session, pipeline_id, version)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{pipeline_id}/export",
    response_model=PipelineExport,
    # ``bundled_agents`` defaults to ``None`` on non-bundle exports;
    # ``exclude_none`` strips it from the JSON so Phase 1 wire format
    # stays exactly the way it was — no ``"bundled_agents": null``
    # noise in pipeline-only exports, and the field appears only when
    # the operator opted in via ``?bundle=true``.
    response_model_exclude_none=True,
)
def export_pipeline(
    pipeline_id: str,
    bundle: bool = Query(
        default=False,
        description=(
            "When true, include every agent the pipeline references in "
            "``bundled_agents`` so a target installation that doesn't "
            "have them yet can import the whole thing in one shot."
        ),
    ),
    session: Session = Depends(get_session),
) -> PipelineExport:
    """Return a portable JSON shape of the pipeline (#124, #126).

    Strips per-installation fields (id, version, timestamps,
    archived_at) so the result can move between DAP installations
    or be checked into git. ``node.agent_id`` strings still point
    at the *source* installation's agent ids.

    With ``bundle=true`` (#126) the response also carries a
    ``bundled_agents`` map keyed by source agent id. The importer
    creates each agent, builds an ``old_id → new_id`` remap, and
    rewrites every ``node.agent_id`` before persisting the
    pipeline. Without the bundle, a target DB that's missing any
    referenced agent gets the standard 422 from the DAG validator.
    """
    try:
        pipeline = repo.get_pipeline(session, pipeline_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        payload = PipelineExportPayload(
            name=pipeline.name,
            description=pipeline.description,
            schema_version=pipeline.schema_version,
            state_schema_ref=pipeline.state_schema_ref,
            entry_point=pipeline.entry_point,
            nodes=list(pipeline.nodes),
            edges=list(pipeline.edges),
            defaults=pipeline.defaults,
            ui_metadata=pipeline.ui_metadata,
        )
    except ValidationError as exc:
        # Shouldn't happen — stored shape was validated on write —
        # but if a future migration ever introduces a stored shape
        # we can't export, surface it as 500 with details rather
        # than crashing without context.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored pipeline could not be re-validated for export: {exc.errors()}",
        ) from exc

    bundled_agents: dict[str, AgentExportPayload] | None = None
    if bundle:
        # One batched fetch for every unique agent_id in the pipeline
        # — a 2-query lookup regardless of node count (#126 review).
        # ``_check_agents`` at save time guarantees a valid pipeline
        # has no archived references, so any id we don't find here
        # would be a row that vanished after save (race / manual DB
        # tinkering). Skip silently in that case; the importer falls
        # back to "agent not found" 422 on the receiving end if the
        # bundle ends up incomplete.
        agent_ids = sorted({node.agent_id for node in payload.nodes})
        agents_by_id = repo.get_agents_by_ids(session, agent_ids)
        bundled_agents = {
            agent_id: build_agent_export_payload(agents_by_id[agent_id])
            for agent_id in agent_ids
            if agent_id in agents_by_id
        }

    return PipelineExport(
        schema_version=PIPELINE_EXPORT_SCHEMA_VERSION,
        pipeline=payload,
        bundled_agents=bundled_agents,
    )
