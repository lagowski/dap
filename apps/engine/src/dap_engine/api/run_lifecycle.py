"""Run lifecycle action endpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from dap_runtimes import RuntimeRegistry
from dap_types import Run
from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import (
    get_checkpointer,
    get_engine_config,
    get_registry,
    get_run_registry,
    get_session,
    get_session_factory,
)
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.execution import RunRegistry, execute_run_background
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

if TYPE_CHECKING:
    from dap_engine.app import EngineConfig


def register_run_lifecycle_routes(router: APIRouter) -> None:
    """Attach run lifecycle action routes to the owning runs router."""

    router.post("/{run_id}/abort", response_model=Run)(abort_run)
    router.post("/{run_id}/pause", response_model=Run)(pause_run_endpoint)
    router.post("/{run_id}/resume", response_model=Run)(resume_run_endpoint)
    router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)(delete_run_endpoint)


async def delete_run_endpoint(
    run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> None:
    """Delete a run and every row that references it, children-first (#700).

    Owner/admin gated (404 for non-owners — anti-enumeration). 409 if the run
    is still in-flight (abort it first). Records a ``run.deleted`` audit event
    in the same transaction as the cascade, so the trail commits atomically
    with the deletion.
    """
    try:
        repo.delete_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except repo.ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    record_audit_event(
        session,
        user_id=user.id,
        event_type="run.deleted",
        event_data={"run_id": run_id},
    )


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
