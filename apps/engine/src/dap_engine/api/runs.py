"""REST endpoints for runs.

POST /runs (F5+) triggers asynchronous pipeline execution via LangGraph.
Returns 201 with Run immediately; pipeline runs in a background asyncio.Task
tracked by the engine's RunRegistry. Use POST /runs/{id}/abort to cancel,
POST /runs/{id}/pause to suspend (LangGraph checkpoint preserved), and
POST /runs/{id}/resume to continue from the last checkpoint.

retry-node/skip-node endpoints are deferred (require manual graph traversal
via aupdate_state).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_types import NodeExecutionLog, PipelineState, Run, StateSnapshot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import (
    get_checkpointer,
    get_registry,
    get_run_registry,
    get_session,
    get_session_factory,
)
from dap_engine.api.schemas import RunCreateRequest
from dap_engine.execution import PipelineRunner, RunnerError, RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM

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
) -> Run:
    """Trigger asynchronous pipeline execution.

    Returns immediately with the Run row in `running` state. The actual
    execution happens in a background asyncio.Task. Poll the GET endpoints
    for progress, or POST /runs/{id}/abort to cancel.
    """
    pipeline = session.get(PipelineORM, payload.pipeline_id)
    if pipeline is None or pipeline.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline not found: {payload.pipeline_id}",
        )

    target_version = payload.pipeline_version or pipeline.current_version
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

    # Build initial state — merge supplied fields over PipelineState defaults
    state_dict: dict[str, Any] = {
        "run_id": "pending",
        "repo": "",
        "branch": "",
        **payload.initial_state,
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
        pipeline_id=payload.pipeline_id,
        pipeline_version=target_version,
        trigger_source="api",
        initial_state=initial_state,
    )
    # Commit so the background task can see the row in its own session.
    session.commit()
    run_id = run_orm.id

    initial_state_with_run_id = initial_state.model_copy(update={"run_id": run_id})

    # Spawn background task — runs pipeline in its own DB session.
    task = asyncio.create_task(
        _execute_run_background(
            run_id=run_id,
            pipeline_id=payload.pipeline_id,
            pipeline_version=target_version,
            initial_state=initial_state_with_run_id,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            resume=False,
        )
    )
    run_registry.register(run_id, task)

    # Re-fetch run to return current state (running)
    return repo.get_run(session, run_id)


async def _execute_run_background(
    *,
    run_id: str,
    pipeline_id: str,
    pipeline_version: int,
    initial_state: PipelineState | None,
    session_factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    checkpointer: BaseCheckpointSaver[Any],
    resume: bool,
) -> None:
    """Run the pipeline in a fresh DB session and finalize the Run row."""
    try:
        with session_factory() as bg_session:
            pipeline_orm = bg_session.get(PipelineORM, pipeline_id)
            version_orm = bg_session.scalar(
                select(PipelineVersionORM)
                .where(PipelineVersionORM.pipeline_id == pipeline_id)
                .where(PipelineVersionORM.version == pipeline_version)
            )
            if pipeline_orm is None or version_orm is None:
                logger.error(
                    "background run %s: pipeline %s@v%s vanished",
                    run_id,
                    pipeline_id,
                    pipeline_version,
                )
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            runner = PipelineRunner(
                session=bg_session,
                registry=registry,
                checkpointer=checkpointer,
            )
            try:
                final_state = await runner.run(
                    run_id=run_id,
                    pipeline_orm=pipeline_orm,
                    pipeline_version_orm=version_orm,
                    initial_state=initial_state,
                    resume=resume,
                )
            except RunnerError as exc:
                logger.exception("run %s failed: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            final_status = final_state.final_status
            if final_status not in {"success", "failed", "aborted"}:
                final_status = "success"
            repo.finalize_run(bg_session, run_id, final_status=final_status)
            bg_session.commit()
    except asyncio.CancelledError:
        # Cancelled via pause() or abort(). Distinguish via was_paused() flag —
        # paused runs keep their checkpoint and can be resumed (no ended_at);
        # aborted runs are terminal.
        was_paused = run_registry.was_paused(run_id)
        try:
            with session_factory() as cancel_session:
                if was_paused:
                    repo.pause_run(cancel_session, run_id)
                else:
                    repo.finalize_run(cancel_session, run_id, final_status="aborted")
                cancel_session.commit()
        except Exception:
            logger.exception("failed to finalize cancelled run %s", run_id)
        raise  # propagate so the registry sees the cancellation
    except Exception:
        logger.exception("background run %s crashed", run_id)
        try:
            with session_factory() as crash_session:
                repo.finalize_run(crash_session, run_id, final_status="failed")
                crash_session.commit()
        except Exception:
            logger.exception("failed to finalize crashed run %s", run_id)


@router.post("/{run_id}/abort", response_model=Run)
async def abort_run(
    run_id: str,
    session: Session = Depends(get_session),
    run_registry: RunRegistry = Depends(get_run_registry),
) -> Run:
    """Cancel a running pipeline.

    409 if the run is already finalized (success/failed/aborted) or no
    background task is registered for it. Paused runs can also be aborted.
    """
    try:
        run = repo.get_run(session, run_id)
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
    return repo.get_run(session, run_id)


@router.post("/{run_id}/pause", response_model=Run)
async def pause_run_endpoint(
    run_id: str,
    session: Session = Depends(get_session),
    run_registry: RunRegistry = Depends(get_run_registry),
) -> Run:
    """Pause a running pipeline.

    Cancels the background task between node boundaries; the LangGraph
    checkpoint preserves state so the run can be resumed via
    POST /runs/{id}/resume. 409 if the run is not currently running.
    """
    try:
        run = repo.get_run(session, run_id)
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
    return repo.get_run(session, run_id)


@router.post("/{run_id}/resume", response_model=Run)
async def resume_run_endpoint(
    run_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
) -> Run:
    """Resume a paused run from its last LangGraph checkpoint.

    409 if the run is not paused. Spawns a fresh background task using
    the same run_id (= LangGraph thread_id), so execution picks up from
    the last checkpointed node.
    """
    try:
        run = repo.get_run(session, run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

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

    repo.resume_run(session, run_id)
    session.commit()

    task = asyncio.create_task(
        _execute_run_background(
            run_id=run_id,
            pipeline_id=run.pipeline_id,
            pipeline_version=run.pipeline_version,
            initial_state=None,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            resume=True,
        )
    )
    run_registry.register(run_id, task)

    return repo.get_run(session, run_id)


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    pipeline_id: str | None = Query(default=None),
    final_status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_runs(
        session,
        pipeline_id=pipeline_id,
        final_status=final_status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [r.model_dump(mode="json") for r in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: str, session: Session = Depends(get_session)) -> Run:
    try:
        return repo.get_run(session, run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{run_id}/state", response_model=PipelineState)
def get_run_state(run_id: str, session: Session = Depends(get_session)) -> PipelineState:
    try:
        return repo.get_run_state(session, run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{run_id}/state/history", response_model=list[StateSnapshot])
def list_run_state_history(
    run_id: str,
    session: Session = Depends(get_session),
) -> list[StateSnapshot]:
    try:
        return repo.list_run_state_history(session, run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{run_id}/nodes/{node_id}", response_model=NodeExecutionLog)
def get_run_node_log(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
) -> NodeExecutionLog:
    try:
        return repo.get_run_node_log(session, run_id, node_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
