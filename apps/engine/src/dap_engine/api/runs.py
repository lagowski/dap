"""REST endpoints for runs.

POST /runs (F5) triggers synchronous pipeline execution via LangGraph.
GET endpoints provide read-only access; pause/resume/abort/retry/skip
endpoints will arrive in follow-up issues.
"""

from __future__ import annotations

from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_types import NodeExecutionLog, PipelineState, Run, StateSnapshot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_registry, get_session
from dap_engine.api.schemas import RunCreateRequest
from dap_engine.execution import PipelineRunner, RunnerError
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Run)
async def trigger_run(
    payload: RunCreateRequest,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
) -> Run:
    """Trigger synchronous pipeline execution.

    Blocks until the pipeline finishes (mocked adapters: ms; real LLMs: seconds).
    Returns the full Run object with final_status, tokens_used, cost_usd.
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

    # Patch run_id into state so node executors see it
    initial_state = initial_state.model_copy(update={"run_id": run_orm.id})

    runner = PipelineRunner(session=session, registry=registry)
    try:
        final_state = await runner.run(
            run_id=run_orm.id,
            pipeline_orm=pipeline,
            pipeline_version_orm=pipeline_version,
            initial_state=initial_state,
        )
        final_status = final_state.final_status
        if final_status not in {"success", "failed", "aborted"}:
            # If pipeline didn't explicitly set final_status, treat as success.
            final_status = "success"
    except RunnerError as exc:
        repo.finalize_run(session, run_orm.id, final_status="failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    repo.finalize_run(session, run_orm.id, final_status=final_status)
    return repo.get_run(session, run_orm.id)


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
