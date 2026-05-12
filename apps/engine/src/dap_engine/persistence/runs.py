"""Run persistence — Run lifecycle, state snapshots, node-execution logs.

Ownership: read / list helpers (``get_run``, ``list_runs``,
``get_run_state``, ``list_run_state_history``, ``get_run_node_log``)
take the actor's ``user_id`` plus an ``is_admin`` flag. Cross-user
lookups raise ``NotFoundError`` (anti-enumeration — same rule
``agents.py`` / ``pipelines.py`` / ``projects.py`` apply).

Lifecycle primitives (``finalize_run``, ``pause_run``,
``try_claim_resume``, ``try_claim_revive``,
``mark_stale_running_runs_as_failed``) **deliberately** take no
ownership args — they're called from background asyncio tasks and the
engine-startup hook, neither of which has a user context. The
ownership boundary is established by the API route, which gates
``get_run(actor_id, is_admin)`` before invoking any primitive.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Final

from dap_types import (
    NodeExecutionLog,
    PipelineState,
    Run,
    StateSnapshot,
)
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from dap_engine.auth.audit import record_audit_event
from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import (
    NodeExecutionLogORM,
    RunORM,
    StateSnapshotORM,
)


def _ownership_filter(
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[ColumnElement[bool]]:
    """Return ``[]`` for admins, else a single-clause filter for the actor.

    Admins see all rows (including legacy NULL ``user_id`` rows from
    the pre-v0.3 backfill); non-admins only see runs they triggered.
    """
    if is_admin:
        return []
    return [RunORM.user_id == actor_id]


# Statuses considered terminal — once a run lands in any of these,
# ``finalize_run`` / ``pause_run`` short-circuit so a stray late cancel
# can't overwrite the run's recorded outcome (#257).
_TERMINAL_STATUSES: Final = frozenset({"success", "failed", "aborted"})


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
        paused_at_node=run.paused_at_node,
        gate_payload=run.gate_payload,
        node_statuses=(
            node_statuses_override  # type: ignore[arg-type]
            if node_statuses_override is not None
            else run.node_statuses
        ),
        final_status=run.final_status,  # type: ignore[arg-type]
        failure_reason=run.failure_reason,
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
    actor_id: uuid.UUID,
    is_admin: bool,
    pipeline_id: str | None = None,
    final_status: str | None = None,
    project_id: str | None = None,
    only_unscoped: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Run], int]:
    """List runs with optional filters; non-admins only see their own.

    ``project_id``: filter to a specific project's runs.
    ``only_unscoped``: when True, return only runs without a project
    (ad-hoc / legacy). Mutually exclusive with ``project_id``; the
    router enforces the mapping from query string to one of these.
    """
    where_clauses: list[ColumnElement[bool]] = []
    where_clauses.extend(_ownership_filter(actor_id, is_admin))
    if pipeline_id is not None:
        where_clauses.append(RunORM.pipeline_id == pipeline_id)
    if final_status is not None:
        where_clauses.append(RunORM.final_status == final_status)
    if only_unscoped:
        where_clauses.append(RunORM.project_id.is_(None))
    elif project_id is not None:
        where_clauses.append(RunORM.project_id == project_id)

    total = session.scalar(select(func.count()).select_from(RunORM).where(*where_clauses)) or 0

    runs_orm = session.scalars(
        select(RunORM)
        .where(*where_clauses)
        .order_by(RunORM.started_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [_run_from_orm(r) for r in runs_orm], total


def get_run(
    session: Session,
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Run:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
        # Anti-enumeration: cross-user lookup looks indistinguishable
        # from "doesn't exist".
        raise NotFoundError(f"Run not found: {run_id}")
    # Populate node_statuses from execution logs on every detail fetch (#233).
    #
    # The runs.node_statuses column is only ever written at row creation
    # (initialised to {} by create_run) — no runner path writes back to
    # it. Per-node status truth lives in node_execution_logs instead, so
    # we join that table here to derive an up-to-date dict for the
    # detail view. list_runs intentionally skips this join: the per-run
    # overhead is too expensive for bulk queries and node-level detail
    # isn't shown in the list view anyway.
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


def get_run_state(
    session: Session,
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> PipelineState:
    """Return the latest state snapshot, or initial_state if no snapshots yet."""
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
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


def list_run_state_history(
    session: Session,
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[StateSnapshot]:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
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
    user_id: uuid.UUID,
    pipeline_id: str,
    pipeline_version: int,
    trigger_source: str,
    initial_state: PipelineState,
    project_id: str | None = None,
) -> RunORM:
    """Insert a Run row in 'running' state. Caller commits.

    The acting ``user_id`` is required; routes resolve it from the
    authenticated request, tests pass an explicit owner. Writes a
    ``run.triggered`` audit row.
    """
    now = _now()
    run = RunORM(
        id=_new_id(),
        user_id=user_id,
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
    record_audit_event(
        session,
        user_id=user_id,
        event_type="run.triggered",
        event_data={
            "run_id": run.id,
            "pipeline_id": pipeline_id,
            "pipeline_version": pipeline_version,
            "project_id": project_id,
            "trigger_source": trigger_source,
        },
    )
    return run


def _compute_run_totals(session: Session, run_id: str) -> tuple[int, float]:
    """Sum ``tokens_used`` + ``cost_usd`` from a run's node-execution logs.

    Uses SQL aggregation so we don't materialise every log row in memory —
    runs with many nodes can have a lot of logs. Returns ``(tokens, cost)``.
    """
    totals = session.execute(
        select(
            func.coalesce(func.sum(NodeExecutionLogORM.tokens_used), 0),
            func.coalesce(func.sum(NodeExecutionLogORM.cost_usd), 0.0),
        ).where(NodeExecutionLogORM.run_id == run_id)
    ).one()
    return int(totals[0]), float(totals[1])


def finalize_run(
    session: Session,
    run_id: str,
    *,
    final_status: str,
) -> None:
    """Mark a run as completed (success/failed/aborted) and aggregate metrics.

    Idempotent and race-safe (#257): the status transition is an atomic
    ``UPDATE ... WHERE final_status NOT IN <terminal>`` and the rowcount
    decides whether the caller won the claim. A late finaliser whose
    session still holds a stale ``"running"`` cached ORM instance will
    see ``rowcount=0`` because the predicate runs at the database, not
    against the session's identity map — first writer wins.

    Cancel-during-runner-error scenario: the inner ``except RunnerError``
    commits ``finalize_run("failed")`` and the outer ``except
    CancelledError`` handler then calls ``finalize_run("aborted")`` from
    a fresh session; the second call no-ops.
    """
    tokens, cost = _compute_run_totals(session, run_id)
    stmt = (
        update(RunORM)
        .where(
            RunORM.id == run_id,
            RunORM.final_status.notin_(_TERMINAL_STATUSES),
        )
        .values(
            final_status=final_status,
            ended_at=_now(),
            tokens_used=tokens,
            cost_usd=cost,
        )
    )
    result = session.execute(stmt)
    # ``CursorResult.rowcount`` exposed only at runtime; static type is
    # ``Result[Any]`` which doesn't have it — same pattern as
    # ``try_claim_resume`` below.
    if result.rowcount == 0:  # type: ignore[attr-defined]
        # Either the run doesn't exist or it's already terminal —
        # distinguish for the caller. NotFoundError is a programmer
        # error; the no-op return is the expected race outcome.
        if session.get(RunORM, run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")
        return
    session.flush()


def pause_run(
    session: Session,
    run_id: str,
    *,
    paused_at_node: str | None = None,
    gate_payload: dict[str, Any] | None = None,
) -> None:
    """Mark a run as paused without setting ended_at (resumable).

    ``paused_at_node`` records the gate node that triggered the interrupt
    so the dashboard can show a targeted "Approve" action instead of the
    generic "Resume" button (#363).

    ``gate_payload`` stores task assignments and other gate context so the
    dashboard can render them without querying the checkpoint store (#364).

    Idempotent and race-safe (#257), same atomic-UPDATE pattern as
    ``finalize_run``. A late pause attempt on an already-terminal run
    no-ops at the database level so it can't reset ``ended_at`` to
    ``None`` and erase the recorded outcome.
    """
    tokens, cost = _compute_run_totals(session, run_id)
    values: dict[str, object] = {
        "final_status": "paused",
        "tokens_used": tokens,
        "cost_usd": cost,
    }
    if paused_at_node is not None:
        values["paused_at_node"] = paused_at_node
    if gate_payload is not None:
        values["gate_payload"] = gate_payload
    stmt = (
        update(RunORM)
        .where(
            RunORM.id == run_id,
            RunORM.final_status.notin_(_TERMINAL_STATUSES),
        )
        .values(**values)
    )
    result = session.execute(stmt)
    if result.rowcount == 0:  # type: ignore[attr-defined]
        if session.get(RunORM, run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")
        return
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
        .values(
            final_status="running",
            ended_at=None,
            failure_reason=None,
            paused_at_node=None,
            gate_payload=None,
        )
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

    Clears ``failure_reason`` (#260): a revived run has left the failed
    state, so a stale "engine restarted mid-run" diagnostic from the
    previous lifecycle should not stay attached.
    """
    stmt = (
        update(RunORM)
        .where(RunORM.id == run_id, RunORM.final_status.in_(("paused", "failed")))
        .values(final_status="running", ended_at=None, failure_reason=None)
    )
    # session.execute(update(...)) returns CursorResult at runtime — only
    # CursorResult exposes .rowcount, which the static Result[Any] type does not.
    result = session.execute(stmt)
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


def mark_stale_running_runs_as_failed(session: Session, *, reason: str) -> int:
    """Find Run rows still in 'running' state and mark them as failed.

    Called on engine startup to clean up orphans left by crashes / kills.
    Returns the count of runs updated. The diagnostic ``reason`` lands
    in ``runs.failure_reason`` (#260) — earlier code wrote a synthetic
    snapshot with ``node_id="__shutdown__"`` and a ``verification_reason``
    state field, which polluted ``get_run_state_history`` consumers
    that scanned for real node ids.
    """
    runs = session.scalars(
        select(RunORM).where(RunORM.final_status == "running"),
    ).all()
    count = 0
    now = _now()
    for run in runs:
        run.final_status = "failed"
        run.ended_at = now
        run.failure_reason = reason
        count += 1
    session.flush()
    return count


def get_run_node_log(
    session: Session,
    run_id: str,
    node_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> NodeExecutionLog:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
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
