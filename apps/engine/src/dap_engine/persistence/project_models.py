"""Project ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from dap_engine.persistence.model_base import Base


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
