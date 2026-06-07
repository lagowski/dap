"""Read-only REST endpoints for runs."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

from dap_types import NodeExecutionLog, PipelineState, Run, StateSnapshot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_engine_config, get_run_registry, get_session
from dap_engine.api.run_events import stream_run_events
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.users import current_active_user
from dap_engine.diagnostics.error_explainer import (
    ErrorExplanation,
    explain_error_llm,
    explain_node_error,
)
from dap_engine.execution import RunRegistry
from dap_engine.instance_env import load_instance_env
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

if TYPE_CHECKING:
    from dap_engine.app import EngineConfig

logger = logging.getLogger("dap.engine.api.run_reads")

# Terminal final_status values — a run in one of these has, by definition,
# finished executing and should have no active task in the run registry.
_TERMINAL_STATUSES = frozenset({"success", "failed", "aborted"})


def register_run_read_routes(router: APIRouter) -> None:
    """Attach read-only run routes to the owning router."""

    router.get("")(list_runs)
    router.get("/{run_id}", response_model=Run)(get_run)
    router.get("/{run_id}/state", response_model=PipelineState)(get_run_state)
    router.get("/{run_id}/state/history", response_model=list[StateSnapshot])(
        list_run_state_history
    )
    router.get("/{run_id}/nodes", response_model=list[NodeExecutionLog])(list_run_node_logs)
    router.get("/{run_id}/nodes/{node_id}", response_model=NodeExecutionLog)(get_run_node_log)
    router.get("/{run_id}/nodes/{node_id}/explain", response_model=ErrorExplanation)(
        explain_run_node_error
    )
    # SSE stream of run-execution events (#662, Phase 3a). No response_model:
    # the route returns a ``StreamingResponse`` (text/event-stream), and its
    # event schema is documented on ``stream_run_events``.
    router.get("/{run_id}/events")(stream_run_events)


def list_runs(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    pipeline_id: str | None = Query(default=None),
    final_status: list[str] | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    started_from_date: date | None = Query(default=None, alias="from"),
    started_to_date: date | None = Query(default=None, alias="to"),
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
    if (
        started_from_date is not None
        and started_to_date is not None
        and started_from_date > started_to_date
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="from must be on or before to",
        )

    only_unscoped = project_id == "null"
    effective_project_id = None if only_unscoped else project_id
    final_statuses = status_filter if status_filter is not None else final_status
    started_from = (
        datetime.combine(started_from_date, time.min, tzinfo=UTC)
        if started_from_date is not None
        else None
    )
    started_to = (
        datetime.combine(started_to_date, time.max, tzinfo=UTC)
        if started_to_date is not None
        else None
    )
    items, total = repo.list_runs(
        session,
        actor_id=user.id,
        is_admin=user.is_superuser,
        pipeline_id=pipeline_id,
        final_statuses=final_statuses,
        project_id=effective_project_id,
        only_unscoped=only_unscoped,
        started_from=started_from,
        started_to=started_to,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [r.model_dump(mode="json") for r in items],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def get_run(
    run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    run_registry: RunRegistry = Depends(get_run_registry),
) -> Run:
    try:
        run = repo.get_run(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Observability tripwire (#636): a run served in a terminal state must
    # not still have a live task in the registry. If it does, we are looking
    # at cross-run state bleed — log the impossible condition so the next
    # occurrence reveals the mechanism. Log-only: the response is unchanged.
    if run.final_status in _TERMINAL_STATUSES and run_registry.is_running(run_id):
        logger.warning(
            "run %s served terminal state %r (ended_at=%s) while its task is still "
            "active in the run registry — possible cross-run state bleed (#636)",
            run_id,
            run.final_status,
            run.ended_at,
        )

    return run


def get_run_state(
    run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> PipelineState:
    try:
        return repo.get_run_state(session, run_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def list_run_state_history(
    run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> list[StateSnapshot]:
    try:
        return repo.list_run_state_history(
            session, run_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def list_run_node_logs(
    run_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> list[NodeExecutionLog]:
    try:
        return repo.list_run_node_logs(
            session, run_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def get_run_node_log(
    run_id: str,
    node_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> NodeExecutionLog:
    try:
        return repo.get_run_node_log(
            session, run_id, node_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


async def explain_run_node_error(
    run_id: str,
    node_id: str,
    ai: bool = Query(
        default=False,
        description="Ask the configured LLM for a richer explanation (on-demand, #691).",
    ),
    session: Session = Depends(get_session),
    config: EngineConfig = Depends(get_engine_config),
    user: UserORM = Depends(current_active_user),
) -> ErrorExplanation:
    """Explanation + suggested actions for a node's failure (#691).

    Ownership-gated through the node-log read (404 for non-owners). Default is
    the deterministic explainer (no LLM, no secrets). With ``?ai=1`` the
    configured provider is asked for a richer explanation; if no provider is
    configured or the call fails, it falls back to the deterministic one.
    """
    try:
        log = repo.get_run_node_log(
            session, run_id, node_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    deterministic = explain_node_error(log.error_message, runtime_id=log.runtime_id)
    if ai and log.error_message:
        env = {
            **load_instance_env(session, config.crypto.instance_env_vars_key),
            **os.environ,
        }
        llm = await explain_error_llm(log.error_message, runtime_id=log.runtime_id, env=env)
        if llm is not None:
            # Audit the LLM call (#691): the error text + reply may quote
            # conversation content, so we record only the run/node identifiers
            # — enough to attribute the provider call, never its content.
            # Committed before the response is written: the audit is forensic,
            # so recording that the provider was invoked matters even if the
            # client disconnects before receiving the body.
            record_audit_event(
                session,
                user_id=user.id,
                event_type="error_explainer.llm",
                event_data={"run_id": run_id, "node_id": node_id},
            )
            session.commit()
            return llm
    return deterministic
