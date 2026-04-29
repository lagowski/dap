"""REST CRUD for pipelines."""

from __future__ import annotations

from typing import Any

from dap_types import Pipeline
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_session
from dap_engine.api.schemas import PipelineCreate, PipelineUpdate
from dap_engine.execution import ValidationResult, validate_pipeline_dag
from dap_engine.persistence import repository as repo

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
    conflicting writers, etc.) and surface in the 200 path via
    ``Response.headers``-style logging at higher layers if needed.
    """
    result = validate_pipeline_dag(payload, session)
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
    """
    _enforce_validation(_update_to_create_shape(payload), session)
    try:
        return repo.update_pipeline(session, pipeline_id, payload)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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
