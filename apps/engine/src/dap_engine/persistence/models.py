"""SQLAlchemy 2.0 ORM models.

Versioning model:
- `agents` and `pipelines` hold stable identity and a pointer to the current version.
- `agent_versions` and `pipeline_versions` hold the full immutable configuration per
  version. Updating an agent/pipeline always creates a new version row; previous
  versions remain unchanged so runs can reference exact configurations forever.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
