"""Node intervention endpoints for runs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from dap_runtimes import RuntimeRegistry
from dap_types import PipelineDefaults, Run
from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
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
from dap_engine.auth.users import current_active_user
from dap_engine.domain.run_state_machine import (
    RESUMABLE_STATUSES,
    REVIVABLE_STATUSES,
)
from dap_engine.execution import (
    REWIND_RETRY,
    REWIND_SKIP,
    RunRegistry,
    execute_rewind_background,
    execute_run_background,
)
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineVersionORM, UserORM

if TYPE_CHECKING:
    from dap_engine.app import EngineConfig


def register_run_intervention_routes(router: APIRouter) -> None:
    """Attach node-level intervention routes to the owning runs router."""

    router.post(
        "/{run_id}/nodes/{node_id}/approve",
        response_model=Run,
        status_code=status.HTTP_202_ACCEPTED,
    )(approve_gate_endpoint)
    router.post("/{run_id}/nodes/{node_id}/retry", response_model=Run)(retry_node)
    router.post("/{run_id}/nodes/{node_id}/skip", response_model=Run)(skip_node)


async def approve_gate_endpoint(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
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

    * **202** — approval accepted; the run resumes in the background (poll
      ``GET /runs/{id}`` for progress). The endpoint records the gate decision,
      dispatches the resume as a background task, and returns immediately
      without awaiting the next phase (#623).
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
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if run.final_status not in RESUMABLE_STATUSES:
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


async def retry_node(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
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
        instance_env_vars_key=config.instance_env_vars_key,
        allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
        user=user,
    )


async def skip_node(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
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
        instance_env_vars_key=config.instance_env_vars_key,
        allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
        user=user,
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
    instance_env_vars_key: str | None,
    allow_bash_runtime_for_non_admin: bool,
    user: UserORM,
) -> Run:
    """Shared validation + dispatch path for retry-node and skip-node."""
    try:
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if run.final_status not in REVIVABLE_STATUSES:
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
                "Run is no longer paused or failed — another request may have "
                "retried/skipped/resumed it, or the run was aborted/finalized "
                "between validation and here"
            ),
        )
    session.commit()

    task = asyncio.create_task(
        execute_rewind_background(
            run_id=run_id,
            pipeline_id=run.pipeline_id,
            pipeline_version=run.pipeline_version,
            target_node=node_id,
            mode=mode,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            instance_env_vars_key=instance_env_vars_key,
            allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
            actor_is_admin=user.is_superuser,
        )
    )
    run_registry.register(run_id, task)

    session.expire_all()
    return repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
