"""Read-only REST endpoints for runs.

Lifecycle operations (trigger, pause, resume, abort, retry, skip) require
the LangGraph execution engine and are implemented in F5+.
"""

from __future__ import annotations

from typing import Any

from dap_types import NodeExecutionLog, PipelineState, Run, StateSnapshot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_session
from dap_engine.persistence import repository as repo

router = APIRouter(prefix="/runs", tags=["runs"])


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
