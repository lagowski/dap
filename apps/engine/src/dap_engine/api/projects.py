"""REST CRUD for projects (#63) + project-bound pipeline trigger (#66)."""

from __future__ import annotations

from typing import Any

import httpx
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
    EnvVarValidationResult,
    ProjectRunRequest,
    ValidateEnvRequest,
    ValidateEnvResponse,
)
from dap_engine.auth.users import current_active_user
from dap_engine.contracts import ProjectCreate, ProjectUpdate, RunCreateRequest
from dap_engine.execution import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

router = APIRouter(prefix="/projects", tags=["projects"])

_GH_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_")


def _is_github_token(value: str) -> bool:
    """Return True if *value* looks like a GitHub token."""
    return any(value.startswith(p) for p in _GH_TOKEN_PREFIXES)


@router.post("/validate-env", response_model=ValidateEnvResponse)
async def validate_env(payload: ValidateEnvRequest) -> ValidateEnvResponse:
    """Probe GitHub tokens among env vars — best-effort, never blocks save."""
    results: list[EnvVarValidationResult] = []
    for key, value in payload.env_vars.items():
        if not _is_github_token(value):
            results.append(EnvVarValidationResult(key=key, is_token=False))
            continue
        # Token-shaped — validate against GitHub API.
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {value}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=10.0,
                )
            if resp.status_code == httpx.codes.OK:
                login = resp.json().get("login")
                results.append(
                    EnvVarValidationResult(key=key, is_token=True, valid=True, login=login)
                )
            else:
                results.append(
                    EnvVarValidationResult(
                        key=key,
                        is_token=True,
                        valid=False,
                        error=f"GitHub API returned {resp.status_code}",
                    )
                )
        except Exception as exc:
            results.append(
                EnvVarValidationResult(
                    key=key,
                    is_token=True,
                    valid=False,
                    error=f"Validation failed: {exc}",
                )
            )
    return ValidateEnvResponse(results=results)


@router.get("")
def list_projects(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_projects(
        session,
        actor_id=user.id,
        is_admin=user.is_superuser,
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
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.create_project(session, payload, user_id=user.id, is_admin=user.is_superuser)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.get_project(session, project_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.update_project(
            session, project_id, payload, actor_id=user.id, is_admin=user.is_superuser
        )
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
    user: UserORM = Depends(current_active_user),
) -> Response:
    try:
        repo.archive_project(session, project_id, actor_id=user.id, is_admin=user.is_superuser)
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
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Trigger the project's bound pipeline for a given workflow kind (#66).

    Convenience over POST /runs: looks up ``pipeline_id =
    project.pipelines[kind]``, fills the engine's ``project_id`` slot,
    seeds ``repo`` / ``branch`` from the project (overridable per
    call), and delegates to the standard run-trigger flow.

    Ownership: non-admins can only trigger projects they own. The
    project lookup is gated by ``repo.get_project`` — same gate the
    other CRUD endpoints use — so a cross-user probe surfaces 404
    (anti-enumeration), never the structured "Project is archived"
    422 that would leak existence. Sharing the gate avoids drift if
    the ownership rule changes (Copilot review on PR #312).
    """
    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if project.archived_at is not None:
        # 422 — same status as POST /runs returns when an archived project_id
        # is in the payload. Aligning the two trigger paths so clients can
        # share error-handling logic regardless of which endpoint they used. (#205)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    # because we already 422'd above.
    return await trigger_run(
        payload=run_request,
        session=session,
        registry=registry,
        run_registry=run_registry,
        session_factory=session_factory,
        checkpointer=checkpointer,
        user=user,
    )
