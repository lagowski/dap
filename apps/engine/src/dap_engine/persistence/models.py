"""SQLAlchemy 2.0 ORM models.

Versioning model:
- `agents` and `pipelines` hold stable identity and a pointer to the current version.
- `agent_versions` and `pipeline_versions` hold the full immutable configuration per
  version. Updating an agent/pipeline always creates a new version row; previous
  versions remain unchanged so runs can reference exact configurations forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
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
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users (auth — v0.3, see #299)
# ---------------------------------------------------------------------------


class UserORM(SQLAlchemyBaseUserTableUUID, Base):
    """Authenticated user.

    Inherits the ``id`` (UUID), ``email`` (unique), ``hashed_password``,
    ``is_active``, ``is_superuser``, ``is_verified`` columns from
    ``SQLAlchemyBaseUserTableUUID``. We extend with audit timestamps and
    a soft-delete column.

    Naming note: the fastapi-users default ``__tablename__`` is ``user``
    (singular). We override to ``users`` to match the rest of the schema
    (``agents``, ``pipelines``, ``runs``, ``projects``, …).

    Soft-delete: the ``UserManager.delete`` override (in
    ``auth/users.py``) writes ``deleted_at`` + flips ``is_active=False``
    instead of issuing ``DELETE FROM users``. This keeps the row alive
    so any future FK pointing at the user — e.g. the ``user_id`` column
    that lands on ``agents`` / ``pipelines`` / ``projects`` / ``runs`` in
    a follow-up sub-PR — remains valid without cascading data loss.
    Hard delete (admin-initiated, GDPR right-to-erasure) is exposed
    separately in Phase C/E.
    """

    __tablename__ = "users"

    # `default` runs Python-side on ORM `User(...)` construction (which is
    # how fastapi-users creates rows). `onupdate` keeps `updated_at` fresh
    # on subsequent UPDATEs without callers having to remember.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Linked OAuth identities. Eager-loaded ("joined") because fastapi-users'
    # SQLAlchemyUserDatabase reads this list on every login lookup; lazy
    # loading would emit a separate SELECT per request.
    oauth_accounts: Mapped[list[OAuthAccountORM]] = relationship(
        "OAuthAccountORM",
        lazy="joined",
        cascade="all, delete-orphan",
    )


class OAuthAccountORM(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """Linked OAuth identity (GitHub, Google, …).

    Inherits id (UUID), oauth_name, access_token, expires_at,
    refresh_token, account_id, account_email columns from
    SQLAlchemyBaseOAuthAccountTableUUID.

    ``user_id`` is overridden because the parent helper hardcodes
    ``ForeignKey("user.id", …)`` (singular table name) but our user
    table is ``users`` (plural — see UserORM.__tablename__). Without
    the override, SQLAlchemy raises NoReferencedTableError on import.
    """

    __tablename__ = "oauth_accounts"

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            GUID,
            ForeignKey("users.id", ondelete="cascade"),
            nullable=False,
        )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentORM(Base):
    """Logical agent — stable identity + pointer to current version."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[AgentVersionORM]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentVersionORM.version",
    )


class AgentVersionORM(Base):
    """Immutable agent version — full configuration snapshot.

    Includes display name so version history is fully recoverable even when
    the agent is renamed.
    """

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False)
    runtime_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON column stores ``list[str]`` (PipelineState field names) as of
    # v0.5; older rows may still hold a dict — the Pydantic Agent model
    # coerces those at read-time. See packages/types/.../agent.py.
    input_schema: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_schema: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    budget_limit_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=60_000)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agent: Mapped[AgentORM] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


class PipelineORM(Base):
    """Logical pipeline — stable identity + pointer to current version."""

    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
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

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    working_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")

    pipelines: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    env_vars: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

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
        # ``list_runs`` unfiltered: ORDER BY started_at DESC (#251).
        Index("ix_runs_started_at", "started_at"),
        # Project-scoped runs view: ``WHERE project_id = ? ORDER BY started_at
        # DESC``. Composite folds filter + sort into one index walk on SQLite,
        # which can't bitmap-intersect single-column indexes (#251).
        Index("ix_runs_project_started", "project_id", "started_at"),
        # Per-pipeline runs view: same shape with ``pipeline_id`` (#251).
        Index("ix_runs_pipeline_started", "pipeline_id", "started_at"),
    )


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
