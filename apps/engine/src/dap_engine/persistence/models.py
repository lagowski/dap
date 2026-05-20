"""SQLAlchemy 2.0 ORM models.

Versioning model:
- `agents` and `pipelines` hold stable identity and a pointer to the current version.
- `agent_versions` and `pipeline_versions` hold the full immutable configuration per
  version. Updating an agent/pipeline always creates a new version row; previous
  versions remain unchanged so runs can reference exact configurations forever.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dap_engine.persistence.agent_models import AgentORM, AgentVersionORM
from dap_engine.persistence.auth_models import (
    ApiTokenORM,
    AuditLogORM,
    OAuthAccountORM,
    UserORM,
)
from dap_engine.persistence.model_base import Base
from dap_engine.persistence.settings_models import InstanceEnvVarORM

__all__ = [
    "AgentORM",
    "AgentVersionORM",
    "ApiTokenORM",
    "AuditLogORM",
    "Base",
    "BatchRunORM",
    "InstanceEnvVarORM",
    "NodeExecutionLogORM",
    "OAuthAccountORM",
    "PipelineORM",
    "PipelineVersionORM",
    "ProjectORM",
    "RunORM",
    "StateSnapshotORM",
    "UserORM",
]

# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


class PipelineORM(Base):
    """Logical pipeline — stable identity + pointer to current version."""

    __tablename__ = "pipelines"
    __table_args__ = (Index("ix_pipelines_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[PipelineVersionORM]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineVersionORM.version",
    )


class PipelineVersionORM(Base):
    """Immutable pipeline version — DAG snapshot.

    Includes name and description so version history reflects the metadata
    at the time of each save.
    """

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id",
            "version",
            name="uq_pipeline_versions_pipeline_version",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("pipelines.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="langgraph/1.0")
    state_schema_ref: Mapped[str] = mapped_column(String, nullable=False)
    entry_point: Mapped[str] = mapped_column(String, nullable=False)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    defaults: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ui_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    backend_profiles: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pipeline: Mapped[PipelineORM] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Projects (v0.6) — workspace + workflow bindings
# ---------------------------------------------------------------------------


class ProjectORM(Base):
    """A project: working directory + binding of workflow kinds to pipelines.

    Single unversioned table — each project has one current-state row that
    is updated in place (only agents and pipelines are versioned). Bindings
    live in the JSON ``pipelines`` column; layered env vars in ``env_vars``.
    Both validated at write time by ``repo.create_project`` / ``update_project``.
    """

    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    working_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")

    pipelines: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    env_vars: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    auto_approve_nodes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Runs (no schema change in F2)
# ---------------------------------------------------------------------------


class RunORM(Base):
    """Pipeline execution instance — immutable FK to pipeline_versions row."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    # Owning project (v0.6, #64). FK with NO cascade — archiving a
    # project leaves its runs intact for historical inspection.
    # Nullable for ad-hoc / legacy runs triggered without a project.
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("projects.id"), nullable=True, default=None
    )
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[int] = mapped_column(Integer, nullable=False)

    trigger_source: Mapped[str] = mapped_column(String, nullable=False)
    initial_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    current_node: Mapped[str | None] = mapped_column(String, nullable=True)
    paused_at_node: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    gate_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    node_statuses: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    final_status: Mapped[str] = mapped_column(String, nullable=False)
    # Operator-facing reason for a non-success terminal status. Populated by
    # ``mark_stale_running_runs_as_failed`` on engine restart (#260) so the
    # dashboard / CLI can show why a run was forcibly terminated; ``None``
    # for runs that finished cleanly or were aborted/paused via a normal
    # operator action.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    state_snapshots: Mapped[list[StateSnapshotORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StateSnapshotORM.timestamp",
    )
    node_logs: Mapped[list[NodeExecutionLogORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="NodeExecutionLogORM.started_at",
    )

    __table_args__ = (
        # ``list_runs`` unfiltered/admin and date-window pages:
        # ``ORDER BY started_at DESC`` plus optional started_at range (#251).
        Index("ix_runs_started_at", "started_at"),
        # Project-scoped runs view: ``WHERE project_id = ? ORDER BY started_at
        # DESC``. Composite folds filter + sort into one index walk on SQLite,
        # which can't bitmap-intersect single-column indexes. final_status /
        # ownership filters are residual predicates unless query evidence shows
        # a more selective composite is worth the write cost (#251, #537).
        Index("ix_runs_project_started", "project_id", "started_at"),
        # Per-pipeline runs view: same shape with ``pipeline_id`` (#251).
        Index("ix_runs_pipeline_started", "pipeline_id", "started_at"),
        # Owner-scoped listings (admin cross-user view + dashboard's
        # "my runs" tab in Phase B). The composites above lead with
        # project_id / pipeline_id, so they don't help the user-only
        # filter; this single-column index keeps the access pattern
        # symmetric with the other resource tables. A future
        # (user_id, started_at) index needs production query evidence,
        # because it would duplicate part of this access path (#299, #537).
        Index("ix_runs_user_id", "user_id"),
    )


class BatchRunORM(Base):
    """Sequential batch of pipeline executions, one per issue number (#473)."""

    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("projects.id"), nullable=True, default=None
    )
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    issue_numbers: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    stop_on_failure: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    auto_approve: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    current_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_batch_runs_user_id", "user_id"),)


class StateSnapshotORM(Base):
    __tablename__ = "state_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    run: Mapped[RunORM] = relationship(back_populates="state_snapshots")


class NodeExecutionLogORM(Base):
    __tablename__ = "node_execution_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prompt_xml: Mapped[str] = mapped_column(Text, nullable=False)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[RunORM] = relationship(back_populates="node_logs")

    __table_args__ = (
        # ``get_run`` populates node_statuses by ``WHERE run_id = ? ORDER BY
        # started_at`` on every detail fetch — high-frequency polling (#251).
        Index("ix_node_execution_logs_run_started", "run_id", "started_at"),
    )
