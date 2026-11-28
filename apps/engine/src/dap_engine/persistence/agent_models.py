"""Agent ORM models."""

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

from dap_engine.persistence.model_base import Base


class AgentORM(Base):
    """Logical agent — stable identity + pointer to current version."""

    __tablename__ = "agents"
    __table_args__ = (
        # Listing endpoints filter by user_id ("my agents") on every page;
        # keep the index aligned with the dominant access pattern.
        Index("ix_agents_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of the agent. CASCADE on user delete is intentional — a user
    # row that's been hard-deleted (admin / GDPR path, Phase C/E) takes
    # their owned data with it. Soft-deleted users keep their rows.
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
