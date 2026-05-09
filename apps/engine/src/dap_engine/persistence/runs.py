"""Run persistence — Run lifecycle, state snapshots, node-execution logs."""

from __future__ import annotations

from collections.abc import Sequence

from dap_types import (
    NodeExecutionLog,
    PipelineState,
    Run,
    StateSnapshot,
)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import (
    NodeExecutionLogORM,
    RunORM,
    StateSnapshotORM,
)


def _run_from_orm(
    run: RunORM,
    *,
    node_statuses_override: dict[str, str] | None = None,
) -> Run:
    return Run(
        id=run.id,
        project_id=run.project_id,
        pipeline_id=run.pipeline_id,
        pipeline_version=run.pipeline_version,
        trigger_source=run.trigger_source,  # type: ignore[arg-type]
        initial_state=PipelineState.model_validate(run.initial_state),
        current_node=run.current_node,
        node_statuses=(
            node_statuses_override  # type: ignore[arg-type]
            if node_statuses_override is not None
            else run.node_statuses
        ),
        final_status=run.final_status,  # type: ignore[arg-type]
        started_at=run.started_at,
        ended_at=run.ended_at,
        tokens_used=run.tokens_used,
        cost_usd=run.cost_usd,
    )


def _state_snapshot_from_orm(snapshot: StateSnapshotORM) -> StateSnapshot:
    return StateSnapshot(
        id=snapshot.id,
        run_id=snapshot.run_id,
        node_id=snapshot.node_id,
        timestamp=snapshot.timestamp,
        state=PipelineState.model_validate(snapshot.state),
    )


def _node_log_from_orm(log: NodeExecutionLogORM) -> NodeExecutionLog:
    return NodeExecutionLog(
        id=log.id,
        run_id=log.run_id,
        node_id=log.node_id,
        agent_id=log.agent_id,
        runtime_id=log.runtime_id,
        started_at=log.started_at,
        ended_at=log.ended_at,
        prompt_xml=log.prompt_xml,
        stdout=log.stdout,
        stderr=log.stderr,
        output_json=log.output_json,
        tokens_used=log.tokens_used,
        cost_usd=log.cost_usd,
        duration_ms=log.duration_ms,
        status=log.status,  # type: ignore[arg-type]
        error_message=log.error_message,
        extra_data=log.extra_data,
    )


def list_runs(
    session: Session,
    *,
    pipeline_id: str | None = None,
    final_status: str | None = None,
    project_id: str | None = None,
    only_unscoped: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Run], int]:
    """List runs with optional filters.

    ``project_id``: filter to a specific project's runs.
    ``only_unscoped``: when True, return only runs without a project
    (ad-hoc / legacy). Mutually exclusive with ``project_id``; the
    router enforces the mapping from query string to one of these.
    """
    base = select(RunORM)
    count_q = select(func.count()).select_from(RunORM)

    if pipeline_id is not None:
        base = base.where(RunORM.pipeline_id == pipeline_id)
        count_q = count_q.where(RunORM.pipeline_id == pipeline_id)
    if final_status is not None:
        base = base.where(RunORM.final_status == final_status)
        count_q = count_q.where(RunORM.final_status == final_status)
    if only_unscoped:
        base = base.where(RunORM.project_id.is_(None))
        count_q = count_q.where(RunORM.project_id.is_(None))
    elif project_id is not None:
        base = base.where(RunORM.project_id == project_id)
        count_q = count_q.where(RunORM.project_id == project_id)

    total = session.scalar(count_q) or 0

    runs_orm = session.scalars(
        base.order_by(RunORM.started_at.desc()).offset(offset).limit(limit)
    ).all()
    return [_run_from_orm(r) for r in runs_orm], total


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    # Populate node_statuses from execution logs on every detail fetch (#233).
    #
    # python-func pipelines never write back to runs.node_statuses (the runner
    # only updates that column for LangGraph-native pipelines); all completed
    # nodes land in node_execution_logs instead.  We join the logs table here
    # so GET /runs/{id} always reflects the true per-node state regardless of
    # pipeline runtime.  list_runs intentionally skips this join — the per-run
    # overhead would be too expensive for bulk queries and node-level detail
    # is not shown in the list view.
    logs = session.scalars(
        select(NodeExecutionLogORM)
        .where(NodeExecutionLogORM.run_id == run_id)
        .order_by(NodeExecutionLogORM.started_at)
    ).all()
    # Compute into a local dict — do NOT mutate run.node_statuses.
    # The session is committed on success in get_session() so any ORM
    # attribute write would turn this read endpoint into a DB write,
    # causing unexpected churn during polling.
    node_statuses_override = {log.node_id: log.status for log in logs} if logs else None
    return _run_from_orm(run, node_statuses_override=node_statuses_override)


def get_run_state(session: Session, run_id: str) -> PipelineState:
    """Return the latest state snapshot, or initial_state if no snapshots yet."""
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")

    latest = session.scalar(
        select(StateSnapshotORM)
        .where(StateSnapshotORM.run_id == run_id)
        .order_by(StateSnapshotORM.timestamp.desc())
        .limit(1)
    )
    if latest is None:
        return PipelineState.model_validate(run.initial_state)
    return PipelineState.model_validate(latest.state)


def list_run_state_history(session: Session, run_id: str) -> list[StateSnapshot]:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    snapshots = session.scalars(
        select(StateSnapshotORM)
        .where(StateSnapshotORM.run_id == run_id)
        .order_by(StateSnapshotORM.timestamp)
    ).all()
    return [_state_snapshot_from_orm(s) for s in snapshots]


def create_run(
    session: Session,
    *,
    pipeline_id: str,
    pipeline_version: int,
    trigger_source: str,
    initial_state: PipelineState,
    project_id: str | None = None,
) -> RunORM:
    """Insert a Run row in 'running' state. Caller commits."""
    now = _now()
    run = RunORM(
        id=_new_id(),
        project_id=project_id,
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
        trigger_source=trigger_source,
        initial_state=initial_state.model_dump(mode="json"),
        current_node=None,
        node_statuses={},
        final_status="running",
        started_at=now,
        ended_at=None,
        tokens_used=0,
        cost_usd=0.0,
    )
    session.add(run)
    session.flush()
    return run


def _aggregate_run_metrics(session: Session, run: RunORM) -> None:
    """Recompute tokens_used + cost_usd on a RunORM from its node-execution logs.

    Uses SQL aggregation so we don't materialize every log row in memory —
    runs with many nodes can have a lot of logs.
    """
    totals = session.execute(
        select(
            func.coalesce(func.sum(NodeExecutionLogORM.tokens_used), 0),
            func.coalesce(func.sum(NodeExecutionLogORM.cost_usd), 0.0),
        ).where(NodeExecutionLogORM.run_id == run.id)
    ).one()
    run.tokens_used = int(totals[0])
    run.cost_usd = float(totals[1])


def finalize_run(
    session: Session,
    run_id: str,
    *,
    final_status: str,
    final_state: PipelineState | None = None,
) -> None:
    """Mark a run as completed (success/failed/aborted) and aggregate metrics."""
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    run.final_status = final_status
    run.ended_at = _now()
    _aggregate_run_metrics(session, run)

    if final_state is not None:
        # Persist final state by overwriting the run's recorded final_status
        # but the per-node snapshots remain authoritative.
        run.current_node = None

    session.flush()


def pause_run(session: Session, run_id: str) -> None:
    """Mark a run as paused without setting ended_at (resumable)."""
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    run.final_status = "paused"
    # Aggregate metrics so the dashboard reflects work-done-so-far.
    _aggregate_run_metrics(session, run)
    session.flush()


def try_claim_resume(session: Session, run_id: str) -> bool:
    """Atomically transition a paused run to running. Returns True on success.

    Used by ``/runs/{id}/resume`` and ``/runs/{id}/nodes/{n}/approve`` to
    prevent the TOCTOU race where two concurrent requests both pass a
    Python-side ``final_status == "paused"`` check, both spawn background
    tasks, and the second ``run_registry.register`` raises with an orphan
    asyncio.Task already in flight. (#185)

    Returns ``False`` if no row matched — either the run id doesn't exist or
    its ``final_status`` is no longer ``"paused"`` (a concurrent caller won
    the claim, or the run was finalized between request validation and here).
    The caller distinguishes those cases with a separate existence check
    when it matters; for the resume path, both produce a 409 response.
    """
    stmt = (
        update(RunORM)
        .where(RunORM.id == run_id, RunORM.final_status == "paused")
        .values(final_status="running", ended_at=None)
    )
    # session.execute(update(...)) returns CursorResult at runtime — only
    # CursorResult exposes .rowcount, which the static Result[Any] type does not.
    result = session.execute(stmt)
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


def try_claim_revive(session: Session, run_id: str) -> bool:
    """Atomically transition a paused-or-failed run to running. Returns True on success.

    Used by ``/runs/{id}/nodes/{n}/retry`` and ``.../skip`` — same TOCTOU
    safety as ``try_claim_resume`` (#185), but accepts ``failed`` as a
    starting state so a node-level intervention can put a terminated run
    back into motion.
    """
    stmt = (
        update(RunORM)
        .where(RunORM.id == run_id, RunORM.final_status.in_(("paused", "failed")))
        .values(final_status="running", ended_at=None)
    )
    # session.execute(update(...)) returns CursorResult at runtime — only
    # CursorResult exposes .rowcount, which the static Result[Any] type does not.
    result = session.execute(stmt)
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


def mark_stale_running_runs_as_failed(session: Session, *, reason: str) -> int:
    """Find Run rows still in 'running' state and mark them as failed.

    Called on engine startup to clean up orphans left by crashes / kills.
    Returns the count of runs updated.
    """
    runs = session.scalars(
        select(RunORM).where(RunORM.final_status == "running"),
    ).all()
    count = 0
    now = _now()
    for run in runs:
        run.final_status = "failed"
        run.ended_at = now
        # Persist reason via a synthetic snapshot — simpler than schema change.
        snapshot = StateSnapshotORM(
            id=_new_id(),
            run_id=run.id,
            node_id="__shutdown__",
            timestamp=now,
            state={**run.initial_state, "final_status": "failed", "verification_reason": reason},
        )
        session.add(snapshot)
        count += 1
    session.flush()
    return count


def get_run_node_log(session: Session, run_id: str, node_id: str) -> NodeExecutionLog:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")

    log = session.scalar(
        select(NodeExecutionLogORM)
        .where(NodeExecutionLogORM.run_id == run_id)
        .where(NodeExecutionLogORM.node_id == node_id)
        .order_by(NodeExecutionLogORM.started_at.desc())
        .limit(1)
    )
    if log is None:
        raise NotFoundError(f"Node log not found: {run_id}/{node_id}")
    return _node_log_from_orm(log)
