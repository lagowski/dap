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

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from dap_types import (
    NodeExecutionLog,
    NodeOutputChunk,
    PipelineState,
    Run,
    StateSnapshot,
)
from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.orm import Session

from dap_engine.auth.audit import record_audit_event
from dap_engine.persistence._common import ConflictError, NotFoundError, _new_id, _now
from dap_engine.persistence.models import (
    NodeExecutionLogORM,
    NodeOutputChunkORM,
    RunORM,
    StateSnapshotORM,
)

logger = logging.getLogger("dap.engine.persistence.runs")


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
        gate_expires_at=run.gate_expires_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        tokens_used=run.tokens_used,
        cost_usd=run.cost_usd,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _state_snapshot_from_orm(snapshot: StateSnapshotORM) -> StateSnapshot:
    return StateSnapshot(
        id=snapshot.id,
        run_id=snapshot.run_id,
        node_id=snapshot.node_id,
        timestamp=snapshot.timestamp,
        state=PipelineState.model_validate(snapshot.state),
    )


def _output_chunk_from_orm(chunk: NodeOutputChunkORM) -> NodeOutputChunk:
    return NodeOutputChunk(
        id=chunk.id,
        run_id=chunk.run_id,
        node_id=chunk.node_id,
        execution_id=chunk.execution_id,
        stream=chunk.stream,
        content=chunk.content,
        created_at=chunk.created_at,
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
    final_statuses: Sequence[str] | None = None,
    project_id: str | None = None,
    only_unscoped: bool = False,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
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
    statuses = list(final_statuses or ([final_status] if final_status is not None else []))
    if statuses:
        where_clauses.append(RunORM.final_status.in_(statuses))
    if only_unscoped:
        where_clauses.append(RunORM.project_id.is_(None))
    elif project_id is not None:
        where_clauses.append(RunORM.project_id == project_id)
    if started_from is not None:
        where_clauses.append(RunORM.started_at >= started_from)
    if started_to is not None:
        where_clauses.append(RunORM.started_at <= started_to)

    total = session.scalar(select(func.count()).select_from(RunORM).where(*where_clauses)) or 0

    runs_orm = session.scalars(
        select(RunORM)
        .where(*where_clauses)
        .order_by(RunORM.started_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [_run_from_orm(r) for r in runs_orm], total


def node_executions_for_agent(
    session: Session,
    agent_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[NodeExecutionLogORM], int]:
    """Node executions for an agent across all runs, newest first (#697).

    Ownership-agnostic (impact / debug view) — the caller gates on the agent
    first, same contract as ``pipelines_using_agent``. Returns ORM rows; the
    API maps them to a summary so the heavy ``stdout`` / ``prompt_xml`` stay
    behind the run-detail view.
    """
    where = NodeExecutionLogORM.agent_id == agent_id
    total = session.scalar(select(func.count()).select_from(NodeExecutionLogORM).where(where)) or 0
    rows = session.scalars(
        select(NodeExecutionLogORM)
        .where(where)
        .order_by(NodeExecutionLogORM.started_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), total


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
    # Defensive guard against the cross-run data bleed reported in #636
    # (2026-06-02): a GET /runs/{114_id} returned data from a different
    # run row (#104's ``ended_at`` / ``final_status`` / ``node_statuses``).
    # Root cause wasn't identified during the read-only investigation —
    # none of caching / session re-use / async-race in the candidate
    # list matched the code shape. If SQLAlchemy ever returns a row
    # whose PK doesn't match the requested id (identity-map quirk,
    # connection-pool state leak, response-lifecycle mutation, etc.),
    # refuse to leak cross-run data and surface the bug with full
    # context so we can finally diagnose the actual mechanism.
    if run.id != run_id:
        logger.error(
            "#636 cross-run data leak detected — refusing to return wrong row. "
            "requested_id=%s returned_id=%s returned_user_id=%s "
            "actor_id=%s is_admin=%s session_id=%s "
            "identity_map_size=%s",
            run_id,
            run.id,
            run.user_id,
            actor_id,
            is_admin,
            id(session),
            len(session.identity_map),
        )
        raise RuntimeError(
            f"#636 cross-run leak: requested {run_id}, got {run.id}. "
            "Logged at dap.engine.persistence.runs ERROR level — please "
            "paste the journald entry into the issue."
        )
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


def delete_run(
    session: Session,
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Delete a run and **every** row that references it, children-first (#700).

    Ownership-gated: a non-owner (non-admin) gets ``NotFoundError`` — the same
    anti-enumeration rule as every other run lookup. An in-flight run (not in
    a terminal ``final_status``) raises ``ConflictError`` — abort it first.

    The cascade is explicit and bottom-up so no orphaned rows are ever left
    behind. A run has three child tables and no grandchildren
    (``node_output_chunks``, ``node_execution_logs``, ``state_snapshots`` —
    all FK ``runs.id``). If a new child table is ever added, the #700 smoke
    test (which asserts every child table is empty after delete) will fail
    until it's wired in here. The deletes run in the caller's transaction, so
    they commit atomically with the ``run.deleted`` audit row.
    """
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
        # Anti-enumeration: cross-user delete is indistinguishable from "missing".
        raise NotFoundError(f"Run not found: {run_id}")
    if run.final_status not in _TERMINAL_STATUSES:
        raise ConflictError(
            f"Run {run_id} is {run.final_status!r} (in-flight); abort it before deleting."
        )

    # Children first (no inter-child FKs, so sibling order is irrelevant),
    # then the run row itself.
    session.execute(delete(NodeOutputChunkORM).where(NodeOutputChunkORM.run_id == run_id))
    session.execute(delete(NodeExecutionLogORM).where(NodeExecutionLogORM.run_id == run_id))
    session.execute(delete(StateSnapshotORM).where(StateSnapshotORM.run_id == run_id))
    session.execute(delete(RunORM).where(RunORM.id == run_id))


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
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    audit_data: dict[str, Any] = {
        "run_id": run.id,
        "pipeline_id": pipeline_id,
        "pipeline_version": pipeline_version,
        "project_id": project_id,
        "trigger_source": trigger_source,
    }
    # Record ``dangerously-auto-approve`` (#389) only when the operator
    # opted in — its *presence* in the audit row is the signal that gates
    # were intentionally bypassed. Stamping ``False`` on every row would
    # dilute that signal for log investigations.
    if initial_state.extensions.get("auto_approve") is True:
        audit_data["auto_approve"] = True
    record_audit_event(
        session,
        user_id=user_id,
        event_type="run.triggered",
        event_data=audit_data,
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
    failure_reason: str | None = None,
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

    ``failure_reason`` (#381): when the orchestrator detects a defensive
    failure (e.g. pipeline finished without setting a terminal
    ``final_status``), the reason lands in ``runs.failure_reason`` so
    the dashboard surfaces it next to the red badge. ``None`` leaves
    the column untouched — succeeding runs and aborts don't write a
    reason. Same column shape used by ``mark_stale_running_runs_as_failed``.
    """
    tokens, cost = _compute_run_totals(session, run_id)
    values: dict[str, Any] = {
        "final_status": final_status,
        "ended_at": _now(),
        "tokens_used": tokens,
        "cost_usd": cost,
    }
    if failure_reason is not None:
        values["failure_reason"] = failure_reason
    stmt = (
        update(RunORM)
        .where(
            RunORM.id == run_id,
            RunORM.final_status.notin_(_TERMINAL_STATUSES),
        )
        .values(**values)
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
    gate_expires_at: datetime | None = None,
) -> None:
    """Mark a run as paused without setting ended_at (resumable).

    ``paused_at_node`` records the gate node that triggered the interrupt
    so the dashboard can show a targeted "Approve" action instead of the
    generic "Resume" button (#363).

    ``gate_payload`` stores task assignments and other gate context so the
    dashboard can render them without querying the checkpoint store (#364).

    ``gate_expires_at`` is the deadline for approval (#582). After this
    timestamp, ``mark_expired_gate_runs_as_failed`` will fail the run with
    failure_reason="gate approval timed out".

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
    if gate_expires_at is not None:
        values["gate_expires_at"] = gate_expires_at
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


def mark_expired_gate_runs_as_failed(session: Session) -> int:
    """Fail paused runs whose gate approval deadline has passed (#582).

    Selects runs with ``final_status='paused'`` and ``gate_expires_at < now``.
    Sets ``final_status='failed'``, ``ended_at=now``, and
    ``failure_reason='gate approval timed out'``.  Returns the count updated.

    Called on engine startup alongside ``mark_stale_running_runs_as_failed``
    and can be called periodically by a background task in future iterations.
    Runs with ``gate_expires_at IS NULL`` are not touched (legacy or noop gate).
    """
    now = _now()
    runs = session.scalars(
        select(RunORM).where(
            RunORM.final_status == "paused",
            RunORM.gate_expires_at.is_not(None),
            RunORM.gate_expires_at < now,
        ),
    ).all()
    count = 0
    for run in runs:
        run.final_status = "failed"
        run.ended_at = now
        run.failure_reason = "gate approval timed out"
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


def list_run_node_logs(
    session: Session,
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[NodeExecutionLog]:
    """Return all node execution logs for a run ordered by started_at.

    Non-admins only see logs for their own runs (anti-enumeration: a
    cross-user lookup is indistinguishable from "not found").
    Returns an empty list when the run has no logs yet (e.g. pre-execution).
    """
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if not is_admin and run.user_id != actor_id:
        raise NotFoundError(f"Run not found: {run_id}")
    logs = session.scalars(
        select(NodeExecutionLogORM)
        .where(NodeExecutionLogORM.run_id == run_id)
        .order_by(NodeExecutionLogORM.started_at)
    ).all()
    return [_node_log_from_orm(log) for log in logs]


def append_output_chunk(
    session: Session,
    *,
    run_id: str,
    node_id: str,
    content: str,
    execution_id: str | None = None,
    stream: str = "stdout",
) -> NodeOutputChunk:
    """Append one node-output chunk and return it with a populated ``id``.

    Adapters call this per stdout/stderr flush (Phase 3b-2). The returned
    chunk's ``id`` is the autoincrement monotonic cursor the SSE endpoint
    pages on. ``session.flush()`` populates the id before mapping; the
    caller commits.

    No ownership check: this is a write primitive on the execution path,
    same contract as ``finalize_run`` / ``create_run`` — the ownership
    boundary is established at the API route.
    """
    chunk = NodeOutputChunkORM(
        run_id=run_id,
        node_id=node_id,
        execution_id=execution_id,
        stream=stream,
        content=content,
        created_at=_now(),
    )
    session.add(chunk)
    session.flush()
    return _output_chunk_from_orm(chunk)


def list_output_chunks_since(
    session: Session,
    run_id: str,
    *,
    after_id: int,
    limit: int = 500,
) -> list[NodeOutputChunk]:
    """Return chunks for ``run_id`` with ``id > after_id`` ascending by id.

    The incremental cursor read driving the SSE ``node_log`` stream:
    ``after_id`` is the last chunk id the client has already seen (0 to
    start from the run's first chunk). ``limit`` bounds the page so a
    single tick can't materialise an unbounded backlog.
    """
    chunks = session.scalars(
        select(NodeOutputChunkORM)
        .where(NodeOutputChunkORM.run_id == run_id, NodeOutputChunkORM.id > after_id)
        .order_by(NodeOutputChunkORM.id)
        .limit(limit)
    ).all()
    return [_output_chunk_from_orm(c) for c in chunks]


def latest_output_chunk_id(session: Session, run_id: str) -> int:
    """Return the highest chunk id for ``run_id``, or ``0`` if none exist.

    Used by the SSE endpoint as the connect cursor so a connecting client
    streams only chunks produced *after* connect (no replay of history).
    """
    # coalesce(..., 0) guarantees a 0 (not NULL) for empty runs; the
    # explicit None check only satisfies scalar()'s ``int | None`` return
    # type and never coerces a real (falsy) value to 0.
    result = session.scalar(
        select(func.coalesce(func.max(NodeOutputChunkORM.id), 0)).where(
            NodeOutputChunkORM.run_id == run_id
        )
    )
    return int(result) if result is not None else 0
