"""Pipeline persistence — CRUD on PipelineORM / PipelineVersionORM.

Ownership: every read / write helper takes the actor's ``user_id``
(plus an ``is_admin`` flag for the cross-user admin path). Non-admins
only see their own rows; an attempt to read or mutate someone else's
pipeline raises ``NotFoundError`` (anti-enumeration — same rule
``agents.py`` applies and that ``api-tokens`` already enforces).

Pipelines reference agents by ``node.agent_id`` strings. The DAG
validator (``execution.validator``) only checks for *existence* —
it does **not** enforce agent-level ownership. A user is allowed to
build a pipeline on top of any agent whose id they know; v0.3
ownership scope is the *pipeline* row, not transitive references.
That keeps shared / admin-curated agent libraries usable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from dap_types import Pipeline
from dap_types.pipeline import PipelineDefaults, PipelineEdge, PipelineNode
from sqlalchemy import ColumnElement, func, select, tuple_
from sqlalchemy.orm import Session

from dap_engine.auth.audit import record_audit_event
from dap_engine.contracts import PipelineCreate, PipelineUiMetadataPatch, PipelineUpdate
from dap_engine.persistence._common import NotFoundError, _new_id, _now
from dap_engine.persistence.models import PipelineORM, PipelineVersionORM


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
    return [PipelineORM.user_id == actor_id]


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
        backend_profiles=version.backend_profiles,
    )


def create_pipeline(
    session: Session,
    payload: PipelineCreate,
    *,
    user_id: uuid.UUID,
) -> Pipeline:
    """Create a new pipeline owned by ``user_id`` and write an audit row."""
    now = _now()
    pipeline = PipelineORM(
        id=_new_id(),
        user_id=user_id,
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
        backend_profiles=getattr(payload, "backend_profiles", None),
        created_at=now,
    )
    session.add(pipeline)
    session.add(version)
    session.flush()
    record_audit_event(
        session,
        user_id=user_id,
        event_type="pipeline.created",
        event_data={"pipeline_id": pipeline.id, "name": payload.name},
    )
    return _pipeline_from_orm(pipeline, version, is_current=True)


def _bump_pipeline_version(
    session: Session,
    existing: PipelineORM,
    payload: PipelineCreate,
    user_id: uuid.UUID,
) -> Pipeline:
    """Write a new version under ``existing`` at ``current + 1`` and make it current.

    Carries ``backend_profiles`` onto the new version (unlike
    :func:`update_pipeline`) and records a ``pipeline.updated`` audit row tagged
    ``via=import``. Shared by both the explicit-id and name-match import paths.
    The stored name is refreshed from the payload — an explicit-id import may
    target a pipeline whose name differs from the bundle's.
    """
    now = _now()
    new_version_number = existing.current_version + 1
    existing.name = payload.name
    existing.description = payload.description
    existing.current_version = new_version_number
    existing.updated_at = now
    version = PipelineVersionORM(
        id=_new_id(),
        pipeline_id=existing.id,
        version=new_version_number,
        name=payload.name,
        description=payload.description,
        schema_version=payload.schema_version,
        state_schema_ref=payload.state_schema_ref,
        entry_point=payload.entry_point,
        nodes=[n.model_dump(mode="json") for n in payload.nodes],
        edges=[e.model_dump(mode="json") for e in payload.edges],
        defaults=payload.defaults.model_dump(mode="json"),
        ui_metadata=payload.ui_metadata,
        backend_profiles=getattr(payload, "backend_profiles", None),
        created_at=now,
    )
    session.add(version)
    session.flush()
    record_audit_event(
        session,
        user_id=user_id,
        event_type="pipeline.updated",
        event_data={
            "pipeline_id": existing.id,
            "version": new_version_number,
            "via": "import",
        },
    )
    return _pipeline_from_orm(existing, version, is_current=True)


def import_pipeline(
    session: Session,
    payload: PipelineCreate,
    *,
    user_id: uuid.UUID,
    target_pipeline_id: str | None = None,
) -> Pipeline:
    """Create a pipeline from an import, or bump the version of an existing one (#755).

    Resolution order:

    1. **Explicit target** — if ``target_pipeline_id`` is given and names a
       non-archived pipeline the caller owns, bump *that* pipeline's version,
       regardless of name. A foreign / archived / unknown id is ignored (no
       enumeration leak, no cross-user mutation) and resolution falls through to:
    2. **Name match** — the newest non-archived pipeline the caller owns with the
       same name is bumped to ``current + 1`` under the SAME ``pipeline_id`` — so
       new runs auto-route to the update and the prior version becomes historical.
    3. **No match** — a brand-new pipeline is created.

    Bumps carry ``backend_profiles`` onto the new version. There is no
    "identical → no-op" short-circuit: bundle imports re-create their agents, so
    node ``agent_id``s always differ and an identical-definition check could
    never match anyway.
    """
    if target_pipeline_id is not None:
        target = session.get(PipelineORM, target_pipeline_id)
        if target is not None and target.user_id == user_id and target.archived_at is None:
            return _bump_pipeline_version(session, target, payload, user_id)
        # Unknown / not-owned / archived id → ignore it and fall through to the
        # name-match-or-create path below.

    existing = session.scalars(
        select(PipelineORM)
        .where(PipelineORM.user_id == user_id)
        .where(PipelineORM.name == payload.name)
        .where(PipelineORM.archived_at.is_(None))
        .order_by(PipelineORM.created_at.asc())
    ).first()
    if existing is None:
        return create_pipeline(session, payload, user_id=user_id)

    return _bump_pipeline_version(session, existing, payload, user_id)


def update_pipeline(
    session: Session,
    pipeline_id: str,
    payload: PipelineUpdate,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Pipeline:
    """Update a pipeline. Non-admins can only touch their own rows."""
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None or pipeline.archived_at is not None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
        # Anti-enumeration: cross-user lookup looks indistinguishable
        # from "doesn't exist".
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
    record_audit_event(
        session,
        user_id=actor_id,
        event_type="pipeline.updated",
        event_data={"pipeline_id": pipeline.id, "version": new_version_number},
    )
    return _pipeline_from_orm(pipeline, version, is_current=True)


def update_pipeline_ui_metadata(
    session: Session,
    pipeline_id: str,
    payload: PipelineUiMetadataPatch,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Merge UI metadata into the current version without bumping version."""
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None or pipeline.archived_at is not None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")

    version = _get_pipeline_version_orm(session, pipeline_id, pipeline.current_version)
    existing = version.ui_metadata if isinstance(version.ui_metadata, dict) else {}
    version.ui_metadata = {**existing, **payload.ui_metadata}
    pipeline.updated_at = _now()
    session.flush()
    record_audit_event(
        session,
        user_id=actor_id,
        event_type="pipeline.ui_metadata_updated",
        event_data={
            "pipeline_id": pipeline.id,
            "version": pipeline.current_version,
            "keys": sorted(payload.ui_metadata),
        },
    )


def archive_pipeline(
    session: Session,
    pipeline_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Archive a pipeline. Non-admins can only archive their own rows."""
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if pipeline.archived_at is not None:
        return  # idempotent
    pipeline.archived_at = _now()
    session.flush()
    record_audit_event(
        session,
        user_id=actor_id,
        event_type="pipeline.archived",
        event_data={"pipeline_id": pipeline.id},
    )


def get_pipeline(
    session: Session,
    pipeline_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Pipeline:
    """Fetch a pipeline. Non-admins only see their own."""
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    version = _get_pipeline_version_orm(session, pipeline_id, pipeline.current_version)
    return _pipeline_from_orm(pipeline, version, is_current=True)


def get_pipeline_version(
    session: Session,
    pipeline_id: str,
    version: int,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> Pipeline:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    version_orm = _get_pipeline_version_orm(session, pipeline_id, version)
    return _pipeline_from_orm(
        pipeline,
        version_orm,
        is_current=version == pipeline.current_version,
    )


def list_pipeline_versions(
    session: Session,
    pipeline_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[Pipeline]:
    pipeline = session.get(PipelineORM, pipeline_id)
    if pipeline is None:
        raise NotFoundError(f"Pipeline not found: {pipeline_id}")
    if not is_admin and pipeline.user_id != actor_id:
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
    actor_id: uuid.UUID,
    is_admin: bool,
    archived: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[Sequence[Pipeline], int]:
    where_clauses: list[ColumnElement[bool]] = []
    where_clauses.extend(_ownership_filter(actor_id, is_admin))
    if not archived:
        where_clauses.append(PipelineORM.archived_at.is_(None))

    total = session.scalar(select(func.count()).select_from(PipelineORM).where(*where_clauses)) or 0

    # JOIN on (pipeline_id, current_version): see list_agents in agents.py.
    rows = session.execute(
        select(PipelineORM, PipelineVersionORM)
        .join(
            PipelineVersionORM,
            (PipelineVersionORM.pipeline_id == PipelineORM.id)
            & (PipelineVersionORM.version == PipelineORM.current_version),
        )
        .where(*where_clauses)
        .order_by(PipelineORM.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    items = [_pipeline_from_orm(pipeline, version, is_current=True) for pipeline, version in rows]
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


def _current_pipeline_versions(session: Session) -> list[PipelineVersionORM]:
    """Fetch only the *current* version row of every non-archived pipeline.

    Composite ``(pipeline_id, version)`` IN keeps the version-table scan
    bounded by the number of non-archived pipelines, instead of every
    historical revision. Imported by ``agents.pipelines_using_agent``
    and ``agents.count_pipelines_using_agents``.

    Does **not** filter by ``user_id`` — it powers the agent-side
    "is this agent in use?" hint, which (as documented on
    ``pipelines_using_agent``) requires upstream ownership gating in
    the agents-layer caller. That contract is unchanged by sub-A4b2b.
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
