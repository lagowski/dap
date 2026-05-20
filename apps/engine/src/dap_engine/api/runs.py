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


@router.post("/{run_id}/abort", response_model=Run)
async def abort_run(
    run_id: str,
    session: Session = Depends(get_session),
    run_registry: RunRegistry = Depends(get_run_registry),
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Cancel a running pipeline.

    409 if the run is already finalized (success/failed/aborted) or no
    background task is registered for it. Paused runs can also be aborted.
    """
    try:
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if run.final_status not in {"running", "paused"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not running or paused (final_status={run.final_status})",
        )

    cancelled = await run_registry.abort(run_id)
    if not cancelled:
        # Background task already finished or run is paused with no live task.
        # Mark the run as aborted defensively.
        repo.finalize_run(session, run_id, final_status="aborted")

    # The background task's CancelledError handler finalizes status in a
    # *separate* session. With expire_on_commit=False, our request session
    # still has the stale RunORM cached — expire it so we read fresh state.
    session.expire_all()
    return repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)


@router.post("/{run_id}/pause", response_model=Run)
async def pause_run_endpoint(
    run_id: str,
    session: Session = Depends(get_session),
    run_registry: RunRegistry = Depends(get_run_registry),
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Pause a running pipeline.

    Cancels the background task between node boundaries; the LangGraph
    checkpoint preserves state so the run can be resumed via
    POST /runs/{id}/resume. 409 if the run is not currently running.
    """
    try:
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if run.final_status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not running (final_status={run.final_status})",
        )

    paused = await run_registry.pause(run_id)
    if not paused:
        # Race window: task finished between status check and pause request.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active background task for this run",
        )

    # Pause finalization runs in the background task's session — expire
    # this session's cached RunORM so the response reflects final_status="paused".
    session.expire_all()
    return repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)


@router.post("/{run_id}/resume", response_model=Run)
async def resume_run_endpoint(
    run_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Resume a paused run from its last LangGraph checkpoint.

    409 if the run is not paused. Spawns a fresh background task using
    the same run_id (= LangGraph thread_id), so execution picks up from
    the last checkpointed node.

    **Human gate behaviour**: when the run is paused via ``interrupt_before``
    (pipeline has ``approval_required_nodes`` set), calling ``/resume``
    correctly skips the interrupt and continues execution — no infinite loop.
    Use ``POST /runs/{id}/nodes/{node_id}/approve`` for a semantically
    clearer alternative that also validates the gate node is actually staged.

    .. deprecated-warning::
        If the run was paused by a *python-func* node that called
        ``POST /runs/{id}/pause`` internally (the old ``human_gate`` pattern),
        ``/resume`` re-executes that node and triggers a new pause.  Use
        ``POST /runs/{id}/nodes/{node_id}/skip`` in that case.  The preferred
        approach is ``approval_required_nodes`` + ``interrupt_before``
        (this engine version) which makes ``/resume`` work correctly (#164).
    """
    try:
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Fast-fail with informative current status; the atomic claim below is the
    # actual source of truth for the paused→running transition (#185).
    if run.final_status != "paused":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not paused (final_status={run.final_status})",
        )

    if run_registry.is_running(run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run already has an active background task",
        )

    # Atomic paused→running transition. Two concurrent /resume requests would
    # otherwise both pass the checks above, both spawn background tasks, and
    # the second run_registry.register would raise with an orphan task in
    # flight. Only one row update wins; the loser sees rowcount==0 → 409. (#185)
    if not repo.try_claim_resume(session, run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Run is no longer paused — another request may have resumed it, "
                "or the run was aborted/finalized between validation and here"
            ),
        )
    session.commit()

    task = asyncio.create_task(
        execute_run_background(
            run_id=run_id,
            pipeline_id=run.pipeline_id,
            pipeline_version=run.pipeline_version,
            initial_state=None,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            resume=True,
            instance_env_vars_key=config.instance_env_vars_key,
            allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
            actor_is_admin=user.is_superuser,
        )
    )
    run_registry.register(run_id, task)

    return repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)


register_run_intervention_routes(router)
register_run_read_routes(router)
