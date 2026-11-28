"""Batch run routes mounted under the public /runs router."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dap_engine.app import EngineConfig

from dap_runtimes import RuntimeRegistry
from dap_types.batch_run import BatchRun
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
from dap_engine.contracts import BatchRunCreateRequest
from dap_engine.execution import RunRegistry, execute_batch_run_background
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineVersionORM, UserORM

logger = logging.getLogger("dap.engine.api.run_batches")

router = APIRouter(tags=["runs"])


@router.post("/batch", status_code=status.HTTP_201_CREATED, response_model=BatchRun)
async def trigger_batch_run(
    payload: BatchRunCreateRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
) -> BatchRun:
    """Trigger sequential batch execution: one run per issue number.

    Returns immediately with a BatchRun in 'running' state. Poll
    GET /runs/batch/{id} until status is terminal.
    """
    try:
        pipeline = repo.get_pipeline(
            session, payload.pipeline_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not pipeline.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline not found: {payload.pipeline_id}",
        )

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

    target_version = (
        payload.pipeline_version if payload.pipeline_version is not None else pipeline.version
    )
    pipeline_version_orm = session.scalar(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == payload.pipeline_id)
        .where(PipelineVersionORM.version == target_version)
    )
    if pipeline_version_orm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline version not found: {payload.pipeline_id}@v{target_version}",
        )

    # Build a base initial_state (without issue_number) — project defaults only.
    project_defaults: dict[str, Any] = {}
    if project is not None:
        if project.repo_url:
            project_defaults["repo"] = project.repo_url
        project_defaults["branch"] = project.default_branch

    base_state: dict[str, Any] = {
        "run_id": "pending",
        "repo": "",
        "branch": "",
        **project_defaults,
    }

    batch_run_orm = repo.create_batch_run(
        session,
        user_id=user.id,
        pipeline_id=payload.pipeline_id,
        pipeline_version=target_version,
        project_id=payload.project_id,
        issue_numbers=payload.issue_numbers,
        stop_on_failure=payload.stop_on_failure,
        auto_approve=payload.auto_approve,
    )
    session.commit()
    batch_run_id = batch_run_orm.id

    batch_task = asyncio.create_task(
        execute_batch_run_background(
            batch_run_id=batch_run_id,
            pipeline_id=payload.pipeline_id,
            pipeline_version=target_version,
            project_id=payload.project_id,
            user_id=user.id,
            issue_numbers=payload.issue_numbers,
            stop_on_failure=payload.stop_on_failure,
            auto_approve=payload.auto_approve,
            base_state=base_state,
            session_factory=session_factory,
            registry=registry,
            run_registry=run_registry,
            checkpointer=checkpointer,
            instance_env_vars_key=config.instance_env_vars_key,
            allow_bash_runtime_for_non_admin=config.allow_bash_runtime_for_non_admin,
            actor_is_admin=user.is_superuser,
        )
    )
    run_registry.register(f"batch:{batch_run_id}", batch_task)
    batch_task.add_done_callback(_log_batch_task_result(batch_run_id))

    return repo.get_batch_run(session, batch_run_id)


@router.get("/batch/{batch_run_id}", response_model=BatchRun)
def get_batch_run(
    batch_run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> BatchRun:
    """Poll batch run status and results."""
    try:
        return repo.get_batch_run(session, batch_run_id)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _log_batch_task_result(batch_run_id: str) -> Callable[[asyncio.Task[None]], None]:
    def _log_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "batch run %s background task failed",
                batch_run_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    return _log_result
