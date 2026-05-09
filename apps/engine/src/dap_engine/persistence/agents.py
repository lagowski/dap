"""Agent persistence — CRUD on AgentORM / AgentVersionORM.

Also hosts ``pipelines_using_agent`` / ``count_pipelines_using_agents``:
those are agent-facing queries (rendered on agent endpoints) but
operate on pipeline data, so they reuse the private
``_current_pipeline_versions`` helper from
:mod:`dap_engine.persistence.pipelines`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dap_types import Agent
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from dap_engine.contracts import AgentCreate, AgentUpdate
from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import AgentORM, AgentVersionORM, PipelineORM
from dap_engine.persistence.pipelines import _current_pipeline_versions


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
    where_clauses: list[ColumnElement[bool]] = []
    if not archived:
        where_clauses.append(AgentORM.archived_at.is_(None))
    if role is not None:
        where_clauses.append(AgentORM.role == role)

    total = session.scalar(select(func.count()).select_from(AgentORM).where(*where_clauses)) or 0

    # JOIN on (agent_id, current_version) folds the per-row version
    # lookup into the same query — single round trip, and nothing scales
    # with `limit` in bind-parameter count (which a tuple-IN would).
    rows = session.execute(
        select(AgentORM, AgentVersionORM)
        .join(
            AgentVersionORM,
            (AgentVersionORM.agent_id == AgentORM.id)
            & (AgentVersionORM.version == AgentORM.current_version),
        )
        .where(*where_clauses)
        .order_by(AgentORM.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    items = [_agent_from_orm(agent, version, is_current=True) for agent, version in rows]
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
