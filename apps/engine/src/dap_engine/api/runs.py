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
from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_types import NodeExecutionLog, PipelineDefaults, PipelineState, Run, StateSnapshot
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
from dap_engine.execution import (
    REWIND_RETRY,
    REWIND_SKIP,
    CheckpointNotFoundError,
    PipelineRunner,
    RunnerError,
    RunnerInterrupt,
    RunRegistry,
)
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM, ProjectORM

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

    # When a project is bound, it must exist and be active. Archived
    # projects fail loudly here so users notice instead of getting a
    # half-stamped run that the dashboard can't group cleanly.
    project: ProjectORM | None = None
    if payload.project_id is not None:
        project = session.get(ProjectORM, payload.project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Project not found: {payload.project_id}",
            )
        if project.archived_at is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Project is archived: {payload.project_id}",
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

    # Build initial state — defaults < project context (#65) < caller's
    # initial_state (caller wins). Project supplies ``repo`` from
    # ``repo_url`` and ``branch`` from ``default_branch`` so a project
    # owner doesn't have to repeat them on every trigger.
    project_defaults: dict[str, Any] = {}
    if project is not None:
        if project.repo_url:
            project_defaults["repo"] = project.repo_url
        project_defaults["branch"] = project.default_branch

    state_dict: dict[str, Any] = {
        "run_id": "pending",
        "repo": "",
        "branch": "",
        **project_defaults,
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
        project_id=payload.project_id,
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
            except RunnerInterrupt:
                # Graph paused at an approval-required node (interrupt_before).
                # This is a normal stop — mark run as paused so the operator
                # can resume via POST /runs/{id}/resume or approve via
                # POST /runs/{id}/nodes/{node_id}/approve (#164).
                repo.pause_run(bg_session, run_id)
                bg_session.commit()
                return
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
        run = repo.get_run(session, run_id)
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
            detail="Run state changed during processing — another request resumed it first",
        )
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


@router.post("/{run_id}/nodes/{node_id}/approve", response_model=Run)
async def approve_gate_endpoint(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
) -> Run:
    """Approve a human gate and continue pipeline execution (#164).

    Semantically equivalent to ``POST /runs/{id}/resume`` but communicates
    operator intent explicitly — "I reviewed the gate and approve". The
    ``node_id`` path parameter documents *which* gate was reviewed; the engine
    validates it exists in the pipeline version that produced this run.

    Works correctly with pipelines that use ``approval_required_nodes`` +
    ``interrupt_before``: ``/resume`` and ``/approve`` both call
    ``ainvoke(None, ...)`` which skips the pending interrupt and continues.

    Status codes:

    * **404** — run not found, pipeline version vanished, or ``node_id`` does
      not exist in the pipeline version that produced this run.
    * **409** — run is not paused, run already has an active background task,
      or ``node_id`` exists in the pipeline but is not declared as an
      approval gate (``defaults.approval_required_nodes``). The last case
      prevents audit-log spoofing: previously the path parameter was
      decorative and any node could be approved against any gate (#184).

    .. note::
        For the legacy python-func ``human_gate`` pattern (where the gate node
        itself calls ``POST /runs/{id}/pause``), use
        ``POST /runs/{id}/nodes/{node_id}/skip`` instead — ``/approve`` and
        ``/resume`` would re-execute the gate node and pause again.
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

    # Validate node_id exists in the pipeline version that produced this run.
    version_orm = session.scalar(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == run.pipeline_id)
        .where(PipelineVersionORM.version == run.pipeline_version)
    )
    if version_orm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline version not found: {run.pipeline_id}@v{run.pipeline_version}",
        )
    node_ids = {n["id"] for n in version_orm.nodes}
    if node_id not in node_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node '{node_id}' not in pipeline {run.pipeline_id}@v{run.pipeline_version}",
        )

    # Reject approval against a non-gate node. Without this check, the path
    # parameter was decorative — any existing node could "approve" any gate,
    # so the audit log would record approved=<wrong_node> while the run
    # actually advances at whatever LangGraph's interrupt_before paused on (#184).
    pipeline_defaults = PipelineDefaults.model_validate(version_orm.defaults)
    approval_nodes = set(pipeline_defaults.approval_required_nodes)
    if node_id not in approval_nodes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Node '{node_id}' is not an approval gate in this pipeline "
                f"(defaults.approval_required_nodes={sorted(approval_nodes)})"
            ),
        )

    # Atomic paused→running transition (#185); see /resume for rationale.
    if not repo.try_claim_resume(session, run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run state changed during processing — another request resumed it first",
        )
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


@router.post("/{run_id}/nodes/{node_id}/retry", response_model=Run)
async def retry_node(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
) -> Run:
    """Re-execute a single node and continue forward.

    Rewinds to the LangGraph checkpoint where `node_id` was staged as next,
    branches a new tip from there, and resumes — the named node runs again.
    Useful when a transient failure (rate limit, flake) caused a node to
    fail; the rest of the run can complete without re-running everything.
    """
    return await _do_node_intervention(
        run_id=run_id,
        node_id=node_id,
        mode=REWIND_RETRY,
        session=session,
        registry=registry,
        run_registry=run_registry,
        session_factory=session_factory,
        checkpointer=checkpointer,
    )


@router.post("/{run_id}/nodes/{node_id}/skip", response_model=Run)
async def skip_node(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
) -> Run:
    """Bypass a node and continue with its downstream successors.

    Rewinds to the LangGraph checkpoint where `node_id` was staged as next,
    fakes a no-op completion of that node, and resumes — downstream nodes
    proceed using whatever state existed before the skipped node would have
    run. Useful when a node is broken and the rest of the pipeline can
    still produce a useful outcome.
    """
    return await _do_node_intervention(
        run_id=run_id,
        node_id=node_id,
        mode=REWIND_SKIP,
        session=session,
        registry=registry,
        run_registry=run_registry,
        session_factory=session_factory,
        checkpointer=checkpointer,
    )


async def _do_node_intervention(
    *,
    run_id: str,
    node_id: str,
    mode: str,
    session: Session,
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    session_factory: sessionmaker[Session],
    checkpointer: BaseCheckpointSaver[Any],
) -> Run:
    """Shared validation + dispatch path for retry-node and skip-node."""
    try:
        run = repo.get_run(session, run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if run.final_status not in {"paused", "failed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run is not paused or failed (final_status={run.final_status}); "
                "retry/skip require a stopped run."
            ),
        )

    if run_registry.is_running(run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run already has an active background task",
        )

    # Validate node exists in the pipeline version that produced this run.
    version_orm = session.scalar(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == run.pipeline_id)
        .where(PipelineVersionORM.version == run.pipeline_version)
    )
    if version_orm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(f"Pipeline version not found: {run.pipeline_id}@v{run.pipeline_version}"),
        )
    node_ids = {n["id"] for n in version_orm.nodes}
    if node_id not in node_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Node '{node_id}' not found in pipeline {run.pipeline_id}@v{run.pipeline_version}"
            ),
        )

    # Atomic (paused|failed)→running transition (#185); same TOCTOU class as
    # /resume and /approve, just a wider starting state to allow node-level
    # recovery from a terminated run.
    if not repo.try_claim_revive(session, run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Run state changed during processing — another request "
                "retried/skipped/resumed it first"
            ),
        )
    session.commit()

    task = asyncio.create_task(
        _execute_rewind_background(
            run_id=run_id,
            pipeline_id=run.pipeline_id,
            pipeline_version=run.pipeline_version,
            target_node=node_id,
            mode=mode,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
        )
    )
    run_registry.register(run_id, task)

    session.expire_all()
    return repo.get_run(session, run_id)


async def _execute_rewind_background(
    *,
    run_id: str,
    pipeline_id: str,
    pipeline_version: int,
    target_node: str,
    mode: str,
    session_factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    run_registry: RunRegistry,
    checkpointer: BaseCheckpointSaver[Any],
) -> None:
    """Run a retry/skip rewind in a fresh DB session and finalize the Run row."""
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
                    "rewind run %s: pipeline %s@v%s vanished",
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
                final_state = await runner.rewind_and_run(
                    run_id=run_id,
                    pipeline_orm=pipeline_orm,
                    pipeline_version_orm=version_orm,
                    target_node=target_node,
                    mode=mode,
                )
            except RunnerInterrupt:
                repo.pause_run(bg_session, run_id)
                bg_session.commit()
                return
            except CheckpointNotFoundError as exc:
                logger.warning("rewind run %s: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return
            except RunnerError as exc:
                logger.exception("rewind run %s failed: %s", run_id, exc)
                repo.finalize_run(bg_session, run_id, final_status="failed")
                bg_session.commit()
                return

            final_status = final_state.final_status
            if final_status not in {"success", "failed", "aborted"}:
                final_status = "success"
            repo.finalize_run(bg_session, run_id, final_status=final_status)
            bg_session.commit()
    except asyncio.CancelledError:
        was_paused = run_registry.was_paused(run_id)
        try:
            with session_factory() as cancel_session:
                if was_paused:
                    repo.pause_run(cancel_session, run_id)
                else:
                    repo.finalize_run(cancel_session, run_id, final_status="aborted")
                cancel_session.commit()
        except Exception:
            logger.exception("failed to finalize cancelled rewind run %s", run_id)
        raise
    except Exception:
        logger.exception("background rewind run %s crashed", run_id)
        try:
            with session_factory() as crash_session:
                repo.finalize_run(crash_session, run_id, final_status="failed")
                crash_session.commit()
        except Exception:
            logger.exception("failed to finalize crashed rewind run %s", run_id)


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    pipeline_id: str | None = Query(default=None),
    final_status: str | None = Query(default=None),
    project_id: str | None = Query(
        default=None,
        description=(
            "Filter by project: omit for all runs, supply a project id "
            'for that project only, or pass the literal "null" to return '
            "only ad-hoc / legacy runs without a project."
        ),
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    only_unscoped = project_id == "null"
    effective_project_id = None if only_unscoped else project_id
    items, total = repo.list_runs(
        session,
        pipeline_id=pipeline_id,
        final_status=final_status,
        project_id=effective_project_id,
        only_unscoped=only_unscoped,
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
