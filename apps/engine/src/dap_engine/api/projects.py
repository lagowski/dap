"""REST CRUD for projects (#63) + project-bound pipeline trigger (#66)."""

from __future__ import annotations

from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_types import Project, Run
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import (
    get_checkpointer,
    get_registry,
    get_run_registry,
    get_session,
    get_session_factory,
)
from dap_engine.api.runs import trigger_run
from dap_engine.api.schemas import (
    ProjectCreate,
    ProjectRunRequest,
    ProjectUpdate,
    RunCreateRequest,
)
from dap_engine.execution import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import ProjectORM

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(
    session: Session = Depends(get_session),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_projects(
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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Project)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
) -> Project:
    try:
        return repo.create_project(session, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str, session: Session = Depends(get_session)) -> Project:
    try:
        return repo.get_project(session, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
) -> Project:
    try:
        return repo.update_project(session, project_id, payload)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(
    project_id: str,
    session: Session = Depends(get_session),
) -> Response:
    try:
        repo.archive_project(session, project_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/run/{kind}",
    status_code=status.HTTP_201_CREATED,
    response_model=Run,
)
async def trigger_project_run(
    project_id: str,
    kind: str,
    payload: ProjectRunRequest | None = None,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
) -> Run:
    """Trigger the project's bound pipeline for a given workflow kind (#66).

    Convenience over POST /runs: looks up ``pipeline_id =
    project.pipelines[kind]``, fills the engine's ``project_id`` slot,
    seeds ``repo`` / ``branch`` from the project (overridable per
    call), and delegates to the standard run-trigger flow.
    """
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )
    if project.archived_at is not None:
        # 409 Conflict — the resource exists but its current state
        # forbids the action. Distinct from the 422 ``trigger_run``
        # uses for ``POST /runs`` because here the caller targeted
        # the project explicitly (path param), not as an optional
        # body field.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project is archived: {project_id}",
        )

    bound_pipeline_id = project.pipelines.get(kind)
    if not bound_pipeline_id:
        bound_kinds = sorted(project.pipelines)
        bound_msg = ", ".join(bound_kinds) if bound_kinds else "(none)"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Project '{project_id}' has no pipeline bound to kind "
                f"'{kind}'. Bound kinds: {bound_msg}"
            ),
        )

    body = payload or ProjectRunRequest()
    run_request = RunCreateRequest(
        pipeline_id=bound_pipeline_id,
        pipeline_version=body.pipeline_version,
        project_id=project_id,
        initial_state=body.initial_state,
    )
    # Delegate to the existing trigger flow — same validation, same
    # background-task spawn, same Run shape on the way back. The
    # archived-project check is duplicated there but unreachable
    # because we already 409'd above.
    return await trigger_run(
        payload=run_request,
        session=session,
        registry=registry,
        run_registry=run_registry,
        session_factory=session_factory,
        checkpointer=checkpointer,
    )
