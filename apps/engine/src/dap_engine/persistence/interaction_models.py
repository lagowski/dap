"""Interaction-log ORM model — EU AI Act record-keeping (#722).

A dedicated append-only table, deliberately separate from the security
``audit_log``: different purpose (compliance record of model
interactions vs. security event trail), different retention policy
(configurable purge window vs. keep-forever), and much larger rows
(full redacted transcripts vs. small metadata dicts).

Everything stored here passed through :func:`dap_engine.redaction.redact`
at the persistence boundary — the columns are named ``redacted_*`` so a
reader (and CodeQL) can't mistake them for clear-text content.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dap_engine.persistence.model_base import Base


class InteractionLogORM(Base):
    """One redacted model interaction (assistant turn; later: run/node)."""

    __tablename__ = "interaction_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # Nullable: system-initiated interactions (e.g. scheduled runs) have
    # no acting user; assistant turns always carry one.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # Which feature produced the interaction: "assistant" today; "run" /
    # "node" when the follow-up lands. Free string (not an enum) so new
    # surfaces don't need a migration.
    surface: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The transcript sent to the model, as a list of {role, content}
    # message dicts — content already redacted.
    redacted_request: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
    )
    redacted_response: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Free-form extras (e.g. context keys present, citation count).
    # Attribute named ``extra`` because ``metadata`` is reserved by the
    # SQLAlchemy declarative base; the column keeps the issue's name.
    extra: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    __table_args__ = (
        # The admin view sorts newest-first and the purge filters on
        # created_at; surface is the only other hot filter.
        Index("ix_interaction_log_created", "created_at"),
        Index("ix_interaction_log_surface_created", "surface", "created_at"),
    )
