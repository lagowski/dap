"""Repository layer — all DB queries and mutations live here.

Routes stay thin: validate input → call repository → serialize output.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from dap_types import (
    Agent,
    NodeExecutionLog,
    Pipeline,
    PipelineState,
    Project,
    Run,
    StateSnapshot,
)
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from dap_engine.api.schemas import (
    AgentCreate,
    AgentUpdate,
    PipelineCreate,
    PipelineUpdate,
    ProjectCreate,
    ProjectUpdate,
)
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    NodeExecutionLogORM,
    PipelineORM,
    PipelineVersionORM,
    ProjectORM,
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
        ui_metadata=version.ui_metadata,
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
            else run.node_statuses  # type: ignore[arg-type]
        ),
        final_status=run.final_status,  # type: ignore[arg-type]
        started_at=run.started_at,
        ended_at=run.ended_at,
        tokens_used=run.tokens_used,
        cost_usd=run.cost_usd,
    )


def _project_from_orm(project: ProjectORM) -> Project:
    return Project(
        id=project.id,
        name=project.name,
        description=project.description,
        working_directory=project.working_directory,
        repo_url=project.repo_url,
        default_branch=project.default_branch,
        pipelines=dict(project.pipelines),
        env_vars=dict(project.env_vars),
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
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


def _current_pipeline_versions(session: Session) -> list[PipelineVersionORM]:
    """Fetch only the *current* version row of every non-archived pipeline.

    Composite ``(pipeline_id, version)`` IN keeps the version-table scan
    bounded by the number of non-archived pipelines, instead of every
    historical revision.
    """
    pipelines = session.scalars(select(PipelineORM).where(PipelineORM.archived_at.is_(None))).all()
    if not pipelines:
        return []
    keys = [(p.id, p.current_version) for p in pipelines]
    return list(
        session.scalars(
            select(PipelineVersionORM).where(
                tuple_(PipelineVersionORM.pipeline_id, PipelineVersionORM.version).in_(keys),
            ),
        ).all()
    )


def pipelines_using_agent(session: Session, agent_id: str) -> list[tuple[str, str]]:
    """Return (id, name) of non-archived pipelines whose current version references the agent."""
    out: list[tuple[str, str]] = []
    pipeline_names = {
        p.id: p.name
        for p in session.scalars(select(PipelineORM).where(PipelineORM.archived_at.is_(None))).all()
    }
    for version in _current_pipeline_versions(session):
        for node in version.nodes or []:
            if node.get("agent_id") == agent_id:
                out.append((version.pipeline_id, pipeline_names[version.pipeline_id]))
                break
    return out


def count_pipelines_using_agents(
    session: Session,
    agent_ids: Iterable[str],
) -> dict[str, int]:
    """Batch counterpart of ``pipelines_using_agent`` for the agent list endpoint."""
    target = set(agent_ids)
    counts: dict[str, int] = dict.fromkeys(target, 0)
    if not target:
        return counts
    for version in _current_pipeline_versions(session):
        seen: set[str] = set()
        for node in version.nodes or []:
            aid = node.get("agent_id")
            if aid in target and aid not in seen:
                seen.add(aid)
                counts[aid] += 1
    return counts


def get_agent(session: Session, agent_id: str) -> Agent:
    agent = session.get(AgentORM, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent not found: {agent_id}")
    version = _get_agent_version_orm(session, agent_id, agent.current_version)
    return _agent_from_orm(agent, version, is_current=True)


def get_agents_by_ids(session: Session, agent_ids: Iterable[str]) -> dict[str, Agent]:
    """Fetch multiple agents (current version) in 2 queries total (#126).

    Used by the pipeline bundle exporter, where a pipeline can
    reference N agents and per-id ``get_agent`` calls would cost
    ``2N`` round-trips. ``WHERE id IN (...)`` for the agent rows,
    then ``WHERE agent_id IN (...)`` for the matching version rows
    indexed by ``(agent_id, current_version)`` to pick each agent's
    head version.

    Missing ids are silently absent from the returned dict — callers
    handle them however they want (404 vs skip).
    """
    ids = list(agent_ids)
    if not ids:
        return {}
    agent_rows = session.scalars(
        select(AgentORM).where(AgentORM.id.in_(ids)),
    ).all()
    if not agent_rows:
        return {}
    version_rows = session.scalars(
        select(AgentVersionORM).where(
            AgentVersionORM.agent_id.in_(a.id for a in agent_rows),
        ),
    ).all()
    versions_by_key = {(v.agent_id, v.version): v for v in version_rows}
    out: dict[str, Agent] = {}
    for agent in agent_rows:
        version = versions_by_key.get((agent.id, agent.current_version))
        if version is None:
            continue
        out[agent.id] = _agent_from_orm(agent, version, is_current=True)
    return out


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
        ui_metadata=payload.ui_metadata,
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
        ui_metadata=payload.ui_metadata,
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
# Projects (v0.6) — workspace + workflow bindings
# ---------------------------------------------------------------------------


def _validate_pipeline_bindings(
    session: Session,
    bindings: dict[str, str],
) -> None:
    """Reject bindings that reference unknown / archived / blank pipelines.

    Caller maps the resulting ``ValueError`` to a 422 — this is a
    user-fixable input problem, not a programmer error.

    Blank pipeline ids should already be rejected by the API request
    schema (``ProjectCreate`` / ``ProjectUpdate``); we re-check here so
    internal callers (or future schemas that bypass the validator)
    still fail loudly instead of silently persisting an invalid state.
    """
    if not bindings:
        return
    blank_kinds = sorted({k for k, v in bindings.items() if not v or not v.strip()})
    if blank_kinds:
        msg = f"blank pipeline id for kind(s): {', '.join(blank_kinds)}"
        raise ValueError(msg)

    bound_ids = sorted(set(bindings.values()))
    found = session.scalars(
        select(PipelineORM).where(PipelineORM.id.in_(bound_ids)),
    ).all()
    by_id = {p.id: p for p in found}

    unknown = [pid for pid in bound_ids if pid not in by_id]
    archived = [pid for pid in bound_ids if pid in by_id and by_id[pid].archived_at is not None]

    problems: list[str] = []
    if unknown:
        problems.append(f"unknown pipeline_id(s): {', '.join(unknown)}")
    if archived:
        problems.append(f"archived pipeline_id(s): {', '.join(archived)}")
    if problems:
        raise ValueError("; ".join(problems))


def create_project(session: Session, payload: ProjectCreate) -> Project:
    _validate_pipeline_bindings(session, payload.pipelines)
    now = _now()
    project = ProjectORM(
        id=_new_id(),
        name=payload.name,
        description=payload.description,
        working_directory=payload.working_directory,
        repo_url=payload.repo_url,
        default_branch=payload.default_branch,
        pipelines=dict(payload.pipelines),
        env_vars=dict(payload.env_vars),
        created_at=now,
        updated_at=now,
        archived_at=None,
    )
    session.add(project)
    session.flush()
    return _project_from_orm(project)


def update_project(session: Session, project_id: str, payload: ProjectUpdate) -> Project:
    project = session.get(ProjectORM, project_id)
    if project is None or project.archived_at is not None:
        raise NotFoundError(f"Project not found: {project_id}")
    _validate_pipeline_bindings(session, payload.pipelines)

    project.name = payload.name
    project.description = payload.description
    project.working_directory = payload.working_directory
    project.repo_url = payload.repo_url
    project.default_branch = payload.default_branch
    project.pipelines = dict(payload.pipelines)
    project.env_vars = dict(payload.env_vars)
    project.updated_at = _now()
    session.flush()
    return _project_from_orm(project)


def archive_project(session: Session, project_id: str) -> None:
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    if project.archived_at is not None:
        return  # idempotent
    project.archived_at = _now()
    session.flush()


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    return _project_from_orm(project)


def list_projects(
    session: Session,
    *,
    archived: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Project], int]:
    base = select(ProjectORM)
    count_q = select(func.count()).select_from(ProjectORM)

    if not archived:
        base = base.where(ProjectORM.archived_at.is_(None))
        count_q = count_q.where(ProjectORM.archived_at.is_(None))

    total = session.scalar(count_q) or 0

    projects_orm = session.scalars(
        base.order_by(ProjectORM.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [_project_from_orm(p) for p in projects_orm], total


# ---------------------------------------------------------------------------
# Runs (read-only in F2)
# ---------------------------------------------------------------------------


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
