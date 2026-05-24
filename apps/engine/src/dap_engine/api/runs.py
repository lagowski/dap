"""REST endpoints for runs.

POST /runs (F5+) triggers asynchronous pipeline execution via LangGraph.
Returns 201 with Run immediately; pipeline runs in a background asyncio.Task
tracked by the engine's RunRegistry. Use POST /runs/{id}/abort to cancel,
POST /runs/{id}/pause to suspend (LangGraph checkpoint preserved),
POST /runs/{id}/resume to continue from the last checkpoint, and
POST /runs/{id}/nodes/{node_id}/retry|skip to recover from a per-node
failure on a paused or failed run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING to avoid the cycle with ``dap_engine.app``
    # (app.py imports this module at startup; ``from __future__ import
    # annotations`` keeps the signature lazy so mypy still gets the type
    # but the import doesn't run at module load).
    from dap_engine.app import EngineConfig

from dap_runtimes import RuntimeRegistry
from dap_types import PipelineState, Run
from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import (
    get_checkpointer,
    get_engine_config,
    get_registry,
    get_run_registry,
    get_session,
    get_session_factory,
)
from dap_engine.api.run_batches import router as batch_router
from dap_engine.api.run_interventions import register_run_intervention_routes
from dap_engine.api.run_lifecycle import register_run_lifecycle_routes
from dap_engine.api.run_reads import register_run_read_routes
from dap_engine.api.run_runtime_policy import pipeline_runtime_policy_error
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.contracts import RunCreateRequest
from dap_engine.execution import (
    RunRegistry,
    execute_run_background,
)
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineVersionORM, UserORM

logger = logging.getLogger("dap.engine.api.runs")

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Run)
async def trigger_run(
    payload: RunCreateRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Trigger asynchronous pipeline execution.

    Returns immediately with the Run row in `running` state. The actual
    execution happens in a background asyncio.Task. Poll the GET endpoints
    for progress, or POST /runs/{id}/abort to cancel.

    Ownership: the caller must own the referenced pipeline (and project,
    when set). Both gates surface a 404 / 422 with anti-enumeration
    wording — a foreign pipeline_id looks exactly like a missing id.
    The new run row is stamped with the acting ``user_id``.
    """
    try:
        pipeline = repo.get_pipeline(
            session, payload.pipeline_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not pipeline.is_active:
        # Archived pipeline — 404, same wording as "doesn't exist", so a
        # caller can't tell archived apart from missing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline not found: {payload.pipeline_id}",
        )

    # When a project is bound, it must exist (and be owned by the caller)
    # and be active. Archived projects fail loudly here so users notice
    # instead of getting a half-stamped run that the dashboard can't
    # group cleanly.
    project = None
    if payload.project_id is not None:
        try:
            project = repo.get_project(
                session, payload.project_id, actor_id=user.id, is_admin=user.is_superuser
            )
        except repo.NotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        if project.archived_at is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Project is archived: {payload.project_id}",
            )

    # ``is not None`` rather than ``or`` — the latter coerces ``0`` to
    # the current version, but ``0`` is a malformed user-supplied
    # version we want to surface as 404 below, not silently rewrite.
    # ``RunCreateRequest.pipeline_version`` has no ge=1 validator, so
    # this is the only gate that catches the malformed case
    # (Copilot review on PR #313).
    target_version = (
        payload.pipeline_version if payload.pipeline_version is not None else pipeline.version
    )
    pipeline_version = session.scalar(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == payload.pipeline_id)
        .where(PipelineVersionORM.version == target_version)
    )
    if pipeline_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline version not found: {payload.pipeline_id}@v{target_version}",
        )
    denial = pipeline_runtime_policy_error(
        session,
        pipeline_version,
        is_admin=user.is_superuser,
        allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
    )
    if denial is not None:
        record_audit_event(
            session,
            user_id=user.id,
            event_type="runtime_policy.denied",
            event_data={
                "surface": "runs.trigger",
                "pipeline_id": payload.pipeline_id,
                "pipeline_version": target_version,
                "reason": denial,
            },
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=denial)

    # Build initial state — defaults < project context (#65) < caller's
    # initial_state (caller wins). Project supplies ``repo`` from
    # ``repo_url`` and ``branch`` from ``default_branch`` so a project
    # owner doesn't have to repeat them on every trigger.
    project_defaults: dict[str, Any] = {}
    project_extensions: dict[str, Any] = {}
    if project is not None:
        if project.repo_url:
            project_defaults["repo"] = project.repo_url
        project_defaults["branch"] = project.default_branch
        # Inject into extensions so cortex git-branch node uses it as the PR
        # base branch without requiring callers to repeat it on every trigger.
        project_extensions["branch"] = project.default_branch
        if project.auto_approve_nodes:
            project_extensions["auto_approve_nodes"] = project.auto_approve_nodes

    # Merge extensions: project-level < caller's extensions (caller wins).
    caller_extensions: dict[str, Any] = payload.initial_state.get("extensions") or {}
    merged_extensions = {**project_extensions, **caller_extensions}

    caller_state = dict(payload.initial_state)
    if merged_extensions:
        caller_state["extensions"] = merged_extensions

    state_dict: dict[str, Any] = {
        "run_id": "pending",
        "repo": "",
        "branch": "",
        **project_defaults,
        **caller_state,
    }
    try:
        initial_state = PipelineState.model_validate(state_dict)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid initial_state: {exc.errors()}",
        ) from exc

    run_orm = repo.create_run(
        session,
        user_id=user.id,
        pipeline_id=payload.pipeline_id,
        pipeline_version=target_version,
        trigger_source="api",
        initial_state=initial_state,
        project_id=payload.project_id,
    )
    # Commit so the background task can see the row in its own session.
    session.commit()
    run_id = run_orm.id

    initial_state_with_run_id = initial_state.model_copy(update={"run_id": run_id})

    # Spawn background task — runs pipeline in its own DB session.
    task = asyncio.create_task(
        execute_run_background(
            run_id=run_id,
            pipeline_id=payload.pipeline_id,
            pipeline_version=target_version,
            initial_state=initial_state_with_run_id,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            resume=False,
            instance_env_vars_key=config.instance_env_vars_key,
            allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
            actor_is_admin=user.is_superuser,
        )
    )
    run_registry.register(run_id, task)

    # Re-fetch run to return current state (running)
    return repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)


router.include_router(batch_router)


register_run_lifecycle_routes(router)
register_run_intervention_routes(router)
register_run_read_routes(router)
