"""Repository layer — all DB queries and mutations live here.

Routes stay thin: validate input → call repository → serialize output.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from dap_types import Agent, NodeExecutionLog, Pipeline, PipelineState, Run, StateSnapshot
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dap_engine.api.schemas import AgentCreate, AgentUpdate, PipelineCreate, PipelineUpdate
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    NodeExecutionLogORM,
    PipelineORM,
    PipelineVersionORM,
    RunORM,
    StateSnapshotORM,
)


class NotFoundError(Exception):
    """Raised when an entity is not found by id/version."""


# ---------------------------------------------------------------------------
# Helpers — ORM → Pydantic
# ---------------------------------------------------------------------------


def _agent_from_orm(
    agent: AgentORM,
    version: AgentVersionORM,
    *,
    is_current: bool,
) -> Agent:
    return Agent(
        id=agent.id,
        name=agent.name if is_current else version.name,
        role=agent.role,
        version=version.version,
        runtime_id=version.runtime_id,
        runtime_config=version.runtime_config,
        prompt_template=version.prompt_template,
        input_schema=version.input_schema,
        output_schema=version.output_schema,
        constraints=version.constraints,
        budget_limit_usd=version.budget_limit_usd,
        timeout_ms=version.timeout_ms,
        created_at=agent.created_at,
        updated_at=agent.updated_at if is_current else version.created_at,
        is_active=agent.archived_at is None,
    )


def _pipeline_from_orm(
    pipeline: PipelineORM,
    version: PipelineVersionORM,
    *,
    is_current: bool,
) -> Pipeline:
    return Pipeline(
        id=pipeline.id,
        name=pipeline.name if is_current else version.name,
        description=pipeline.description if is_current else version.description,
        version=version.version,
        schema_version=version.schema_version,  # type: ignore[arg-type]
        state_schema_ref=version.state_schema_ref,
        entry_point=version.entry_point,
        nodes=[PipelineNode.model_validate(n) for n in version.nodes],
        edges=[PipelineEdge.model_validate(e) for e in version.edges],
        defaults=PipelineDefaults.model_validate(version.defaults),
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at if is_current else version.created_at,
        is_active=pipeline.archived_at is None,
    )


def _run_from_orm(run: RunORM) -> Run:
    return Run(
        id=run.id,
        pipeline_id=run.pipeline_id,
        pipeline_version=run.pipeline_version,
        trigger_source=run.trigger_source,  # type: ignore[arg-type]
        initial_state=PipelineState.model_validate(run.initial_state),
        current_node=run.current_node,
        node_statuses=run.node_statuses,  # type: ignore[arg-type]
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
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def create_agent(session: Session, payload: AgentCreate) -> Agent:
    now = _now()
    agent = AgentORM(
        id=_new_id(),
        name=payload.name,
        role=payload.role,
        current_version=1,
        created_at=now,
        updated_at=now,
        archived_at=None,
    )
    version = AgentVersionORM(
        id=_new_id(),
        agent_id=agent.id,
        version=1,
        name=payload.name,
        runtime_id=payload.runtime_id,
        runtime_config=payload.runtime_config,
        prompt_template=payload.prompt_template,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        constraints=payload.constraints,
        budget_limit_usd=payload.budget_limit_usd,
        timeout_ms=payload.timeout_ms,
        created_at=now,
    )
    session.add(agent)
    session.add(version)
    session.flush()
    return _agent_from_orm(agent, version, is_current=True)


def update_agent(session: Session, agent_id: str, payload: AgentUpdate) -> Agent:
    agent = session.get(AgentORM, agent_id)
    if agent is None or agent.archived_at is not None:
        raise NotFoundError(f"Agent not found: {agent_id}")

    now = _now()
    new_version_number = agent.current_version + 1

    if payload.name is not None:
        agent.name = payload.name
    agent.current_version = new_version_number
    agent.updated_at = now

    version = AgentVersionORM(
        id=_new_id(),
        agent_id=agent.id,
        version=new_version_number,
        name=agent.name,
        runtime_id=payload.runtime_id,
        runtime_config=payload.runtime_config,
        prompt_template=payload.prompt_template,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        constraints=payload.constraints,
        budget_limit_usd=payload.budget_limit_usd,
        timeout_ms=payload.timeout_ms,
        created_at=now,
    )
    session.add(version)
    session.flush()
    return _agent_from_orm(agent, version, is_current=True)


def archive_agent(session: Session, agent_id: str) -> None:
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    if agent.archived_at is not None:
        return  # idempotent
    agent.archived_at = _now()
    session.flush()


def get_agent(session: Session, agent_id: str) -> Agent:
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    version = _get_agent_version_orm(session, agent_id, agent.current_version)
    return _agent_from_orm(agent, version, is_current=True)


def get_agent_version(session: Session, agent_id: str, version: int) -> Agent:
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    version_orm = _get_agent_version_orm(session, agent_id, version)
    return _agent_from_orm(agent, version_orm, is_current=version == agent.current_version)


def get_agent_template(session: Session, agent_id: str, version: int | None = None) -> str:
    """Fetch the prompt_template string for an agent (current or specific version)."""
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    target_version = version if version is not None else agent.current_version
    version_orm = _get_agent_version_orm(session, agent_id, target_version)
    return version_orm.prompt_template


def list_agent_versions(session: Session, agent_id: str) -> list[Agent]:
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    versions = session.scalars(
        select(AgentVersionORM)
        .where(AgentVersionORM.agent_id == agent_id)
        .order_by(AgentVersionORM.version)
    ).all()
    return [
        _agent_from_orm(agent, v, is_current=v.version == agent.current_version) for v in versions
    ]


def list_agents(
    session: Session,
    *,
    role: str | None = None,
    archived: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Agent], int]:
    base = select(AgentORM)
    count_q = select(func.count()).select_from(AgentORM)

    if not archived:
        base = base.where(AgentORM.archived_at.is_(None))
        count_q = count_q.where(AgentORM.archived_at.is_(None))
    if role is not None:
        base = base.where(AgentORM.role == role)
        count_q = count_q.where(AgentORM.role == role)

    total = session.scalar(count_q) or 0

    agents_orm = session.scalars(
        base.order_by(AgentORM.created_at.desc()).offset(offset).limit(limit)
    ).all()

    items: list[Agent] = []
    for agent in agents_orm:
        version = _get_agent_version_orm(session, agent.id, agent.current_version)
        items.append(_agent_from_orm(agent, version, is_current=True))

    return items, total


def _get_agent_version_orm(session: Session, agent_id: str, version: int) -> AgentVersionORM:
    version_orm = session.scalar(
        select(AgentVersionORM)
        .where(AgentVersionORM.agent_id == agent_id)
        .where(AgentVersionORM.version == version)
    )
    if version_orm is None:
        raise NotFoundError(f"Agent version not found: {agent_id}@v{version}")
    return version_orm


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def create_pipeline(session: Session, payload: PipelineCreate) -> Pipeline:
    now = _now()
    pipeline = PipelineORM(
        id=_new_id(),
        name=payload.name,
        description=payload.description,
        current_version=1,
        created_at=now,
        updated_at=now,
        archived_at=None,
    )
    version = PipelineVersionORM(
        id=_new_id(),
        pipeline_id=pipeline.id,
        version=1,
        name=payload.name,
        description=payload.description,
        schema_version=payload.schema_version,
        state_schema_ref=payload.state_schema_ref,
        entry_point=payload.entry_point,
        nodes=[n.model_dump(mode="json") for n in payload.nodes],
        edges=[e.model_dump(mode="json") for e in payload.edges],
        defaults=payload.defaults.model_dump(mode="json"),
        created_at=now,
    )
    session.add(pipeline)
    session.add(version)
    session.flush()
    return _pipeline_from_orm(pipeline, version, is_current=True)


def update_pipeline(session: Session, pipeline_id: str, payload: PipelineUpdate) -> Pipeline:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None or pipeline.archived_at is not None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")

    now = _now()
    new_version_number = pipeline.current_version + 1

    if payload.name is not None:
        pipeline.name = payload.name
    if payload.description is not None:
        pipeline.description = payload.description
    pipeline.current_version = new_version_number
    pipeline.updated_at = now

    version = PipelineVersionORM(
        id=_new_id(),
        pipeline_id=pipeline.id,
        version=new_version_number,
        name=pipeline.name,
        description=pipeline.description,
        schema_version=payload.schema_version,
        state_schema_ref=payload.state_schema_ref,
        entry_point=payload.entry_point,
        nodes=[n.model_dump(mode="json") for n in payload.nodes],
        edges=[e.model_dump(mode="json") for e in payload.edges],
        defaults=payload.defaults.model_dump(mode="json"),
        created_at=now,
    )
    session.add(version)
    session.flush()
    return _pipeline_from_orm(pipeline, version, is_current=True)


def archive_pipeline(session: Session, pipeline_id: str) -> None:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if pipeline.archived_at is not None:
        return
    pipeline.archived_at = _now()
    session.flush()


def get_pipeline(session: Session, pipeline_id: str) -> Pipeline:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    version = _get_pipeline_version_orm(session, pipeline_id, pipeline.current_version)
    return _pipeline_from_orm(pipeline, version, is_current=True)


def get_pipeline_version(session: Session, pipeline_id: str, version: int) -> Pipeline:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    version_orm = _get_pipeline_version_orm(session, pipeline_id, version)
    return _pipeline_from_orm(
        pipeline,
        version_orm,
        is_current=version == pipeline.current_version,
    )


def list_pipeline_versions(session: Session, pipeline_id: str) -> list[Pipeline]:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    versions = session.scalars(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == pipeline_id)
        .order_by(PipelineVersionORM.version)
    ).all()
    return [
        _pipeline_from_orm(pipeline, v, is_current=v.version == pipeline.current_version)
        for v in versions
    ]


def list_pipelines(
    session: Session,
    *,
    archived: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Pipeline], int]:
    base = select(PipelineORM)
    count_q = select(func.count()).select_from(PipelineORM)

    if not archived:
        base = base.where(PipelineORM.archived_at.is_(None))
        count_q = count_q.where(PipelineORM.archived_at.is_(None))

    total = session.scalar(count_q) or 0

    pipelines_orm = session.scalars(
        base.order_by(PipelineORM.created_at.desc()).offset(offset).limit(limit)
    ).all()

    items: list[Pipeline] = []
    for pipeline in pipelines_orm:
        version = _get_pipeline_version_orm(session, pipeline.id, pipeline.current_version)
        items.append(_pipeline_from_orm(pipeline, version, is_current=True))

    return items, total


def _get_pipeline_version_orm(
    session: Session,
    pipeline_id: str,
    version: int,
) -> PipelineVersionORM:
    version_orm = session.scalar(
        select(PipelineVersionORM)
        .where(PipelineVersionORM.pipeline_id == pipeline_id)
        .where(PipelineVersionORM.version == version)
    )
    if version_orm is None:
        raise NotFoundError(f"Pipeline version not found: {pipeline_id}@v{version}")
    return version_orm


# ---------------------------------------------------------------------------
# Runs (read-only in F2)
# ---------------------------------------------------------------------------


def list_runs(
    session: Session,
    *,
    pipeline_id: str | None = None,
    final_status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Run], int]:
    base = select(RunORM)
    count_q = select(func.count()).select_from(RunORM)

    if pipeline_id is not None:
        base = base.where(RunORM.pipeline_id == pipeline_id)
        count_q = count_q.where(RunORM.pipeline_id == pipeline_id)
    if final_status is not None:
        base = base.where(RunORM.final_status == final_status)
        count_q = count_q.where(RunORM.final_status == final_status)

    total = session.scalar(count_q) or 0

    runs_orm = session.scalars(
        base.order_by(RunORM.started_at.desc()).offset(offset).limit(limit)
    ).all()
    return [_run_from_orm(r) for r in runs_orm], total


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    return _run_from_orm(run)


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
) -> RunORM:
    """Insert a Run row in 'running' state. Caller commits."""
    now = _now()
    run = RunORM(
        id=_new_id(),
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


def resume_run(session: Session, run_id: str) -> None:
    """Reset a paused run back to running so the background task can take over."""
    run = session.get(RunORM, run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    if run.final_status != "paused":
        msg = f"Run is not paused (final_status={run.final_status})"
        raise ValueError(msg)
    run.final_status = "running"
    run.ended_at = None
    session.flush()


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
