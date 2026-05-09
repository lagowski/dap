"""Project persistence — CRUD on ProjectORM (v0.6 workspace + workflow bindings)."""

from __future__ import annotations

from collections.abc import Sequence

from dap_types import Project
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dap_engine.contracts import ProjectCreate, ProjectUpdate
from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import PipelineORM, ProjectORM


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
