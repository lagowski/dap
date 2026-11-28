"""Interaction log — EU AI Act record-keeping table (#722).

Append-only store of **redacted** model interactions (assistant turns
now; run/node prompts+outputs in the follow-up). Separate from the
security ``audit_log``: different retention policy and much larger rows.

Additive + idempotent — fresh DBs get the table from
``Base.metadata.create_all`` before this revision runs, so the
existence check makes the revision a stamp there; pre-existing dev DBs
get the real CREATE.

Revision ID: 0009_interaction_log
Revises: 0008_node_execution_logs_agent_index
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision: str = "0009_interaction_log"
down_revision: str | None = "0008_node_execution_logs_agent_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "interaction_log"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    op.create_table(
        _TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("user_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surface", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("redacted_request", json_type, nullable=False),
        sa.Column("redacted_response", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("grounded", sa.Boolean(), nullable=True),
        sa.Column("metadata", json_type, nullable=True),
    )
    op.create_index("ix_interaction_log_created", _TABLE, ["created_at"])
    op.create_index("ix_interaction_log_surface_created", _TABLE, ["surface", "created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
