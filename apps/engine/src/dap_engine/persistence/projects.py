"""Project persistence — CRUD on ProjectORM (v0.6 workspace + workflow bindings).

Ownership: every read / write helper takes the actor's ``user_id``
(plus an ``is_admin`` flag for the cross-user admin path). Non-admins
only see their own rows; an attempt to read or mutate someone else's
project raises ``NotFoundError`` (anti-enumeration — same rule
``agents.py`` and ``pipelines.py`` apply).

Projects bind workflow kinds to ``pipeline_id`` strings. The binding
validator (:func:`_validate_pipeline_bindings`) enforces that the
caller owns every pipeline they bind — otherwise a non-admin could
attach a foreign pipeline_id and trigger it via
``POST /projects/{id}/run/{kind}``. Admins bypass that check (same
pattern as the row-level ownership filter).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from dap_types import Project
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from dap_engine.auth.audit import record_audit_event
from dap_engine.contracts import ProjectCreate, ProjectUpdate
from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import PipelineORM, ProjectORM


def _ownership_filter(
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[ColumnElement[bool]]:
    """Return ``[]`` for admins, else a single-clause filter for the actor.

    Centralised so every list query gets the rule without copy-paste.
    Admins see all rows (including legacy NULL ``user_id`` rows from
    the pre-v0.3 backfill); non-admins only see rows they own.
    """
    if is_admin:
        return []
    return [ProjectORM.user_id == actor_id]


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
        auto_approve_nodes=list(project.auto_approve_nodes or []),
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
    )


def _validate_pipeline_bindings(
    session: Session,
    bindings: dict[str, str],
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Reject bindings that reference unknown / archived / blank / foreign pipelines.

    Caller maps the resulting ``ValueError`` to a 422 — this is a
    user-fixable input problem, not a programmer error.

    Blank pipeline ids should already be rejected by the API request
    schema (``ProjectCreate`` / ``ProjectUpdate``); we re-check here so
    internal callers (or future schemas that bypass the validator)
    still fail loudly instead of silently persisting an invalid state.

    Ownership: non-admin callers can only bind pipelines they own.
    Foreign pipeline_ids are surfaced as ``unknown pipeline_id(s)`` —
    same wording the "doesn't exist" case uses — so a probe can't
    distinguish "someone else owns this id" from "no such id" through
    the error body (anti-enumeration; matches the 404 pattern the row-
    level gates use elsewhere).
    """
    if not bindings:
        return
    blank_kinds = sorted({k for k, v in bindings.items() if not v or not v.strip()})
    if blank_kinds:
        msg = f"blank pipeline id for kind(s): {', '.join(blank_kinds)}"
        raise ValueError(msg)

    bound_ids = sorted(set(bindings.values()))
    pipeline_query = select(PipelineORM).where(PipelineORM.id.in_(bound_ids))
    if not is_admin:
        # Non-admins: only pipelines they own are findable. Foreign
        # rows look "unknown" to them — see docstring.
        pipeline_query = pipeline_query.where(PipelineORM.user_id == actor_id)
    found = session.scalars(pipeline_query).all()
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


def create_project(
    session: Session,
    payload: ProjectCreate,
    *,
    user_id: uuid.UUID,
    is_admin: bool,
) -> Project:
    """Create a project owned by ``user_id`` and write an audit row.

    ``is_admin`` rides along so the binding validator can let admins
    attach pipelines owned by anyone (e.g. an admin curating shared
    workflows). For non-admin creators, every bound pipeline_id must
    be owned by the creator.
    """
    if not is_admin and payload.auto_approve_nodes:
        raise PermissionError("auto_approve_nodes requires an admin user")
    _validate_pipeline_bindings(session, payload.pipelines, actor_id=user_id, is_admin=is_admin)
    now = _now()
    project = ProjectORM(
        id=_new_id(),
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        working_directory=payload.working_directory,
        repo_url=payload.repo_url,
        default_branch=payload.default_branch,
        pipelines=dict(payload.pipelines),
        env_vars=dict(payload.env_vars),
        auto_approve_nodes=list(payload.auto_approve_nodes),
        created_at=now,
        updated_at=now,
        archived_at=None,
    )
    session.add(project)
    session.flush()
    record_audit_event(
        session,
        user_id=user_id,
        event_type="project.created",
        event_data={"project_id": project.id, "name": payload.name},
    )
    return _project_from_orm(project)


def update_project(
    session: Session,
    project_id: str,
    payload: ProjectUpdate,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Project:
    """Update a project. Non-admins can only touch their own rows."""
    project = session.get(ProjectORM, project_id)
    if project is None or project.archived_at is not None:
        raise NotFoundError(f"Project not found: {project_id}")
    if not is_admin and project.user_id != actor_id:
        # Anti-enumeration: cross-user lookup looks indistinguishable
        # from "doesn't exist".
        raise NotFoundError(f"Project not found: {project_id}")
    if not is_admin and list(payload.auto_approve_nodes) != list(project.auto_approve_nodes or []):
        raise PermissionError("auto_approve_nodes requires an admin user")
    _validate_pipeline_bindings(session, payload.pipelines, actor_id=actor_id, is_admin=is_admin)

    project.name = payload.name
    project.description = payload.description
    project.working_directory = payload.working_directory
    project.repo_url = payload.repo_url
    project.default_branch = payload.default_branch
    project.pipelines = dict(payload.pipelines)
    project.env_vars = dict(payload.env_vars)
    project.auto_approve_nodes = list(payload.auto_approve_nodes)
    project.updated_at = _now()
    session.flush()
    record_audit_event(
        session,
        user_id=actor_id,
        event_type="project.updated",
        event_data={"project_id": project.id},
    )
    return _project_from_orm(project)


def archive_project(
    session: Session,
    project_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Archive a project. Non-admins can only archive their own rows."""
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    if not is_admin and project.user_id != actor_id:
        raise NotFoundError(f"Project not found: {project_id}")
    if project.archived_at is not None:
        return  # idempotent
    project.archived_at = _now()
    session.flush()
    record_audit_event(
        session,
        user_id=actor_id,
        event_type="project.archived",
        event_data={"project_id": project.id},
    )


def get_project(
    session: Session,
    project_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Project:
    """Fetch a project. Non-admins only see their own."""
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    if not is_admin and project.user_id != actor_id:
        raise NotFoundError(f"Project not found: {project_id}")
    return _project_from_orm(project)


def list_projects(
    session: Session,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
    archived: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Project], int]:
    where_clauses: list[ColumnElement[bool]] = []
    where_clauses.extend(_ownership_filter(actor_id, is_admin))
    if not archived:
        where_clauses.append(ProjectORM.archived_at.is_(None))

    total = session.scalar(select(func.count()).select_from(ProjectORM).where(*where_clauses)) or 0

    projects_orm = session.scalars(
        select(ProjectORM)
        .where(*where_clauses)
        .order_by(ProjectORM.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [_project_from_orm(p) for p in projects_orm], total


def projects_using_pipelines(
    session: Session,
    pipeline_ids: list[str],
) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Reverse-lookup: projects whose workflow bindings reference any of
    ``pipeline_ids``.

    Returns ``(project_id, project_name, [(kind, pipeline_id)])`` per project.
    Ownership-agnostic by design (impact view) — the caller gates on the
    agent / pipeline first, same contract as ``pipelines_using_agent``.
    """
    wanted = set(pipeline_ids)
    if not wanted:
        return []
    out: list[tuple[str, str, list[tuple[str, str]]]] = []
    for project in session.scalars(select(ProjectORM)).all():
        matches = sorted(
            (kind, pid) for kind, pid in (project.pipelines or {}).items() if pid in wanted
        )
        if matches:
            out.append((project.id, project.name, matches))
    return out
