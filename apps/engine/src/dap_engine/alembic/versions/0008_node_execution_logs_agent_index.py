"""Index node_execution_logs by agent for the per-agent activity log (#697).

Backs ``GET /agents/{id}/executions``: ``WHERE agent_id = ? ORDER BY
started_at DESC``. Additive + idempotent — safe to apply on a live DB.

Revision ID: 0008_node_execution_logs_agent_index
Revises: 0007_node_output_chunks
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_node_execution_logs_agent_index"
down_revision: str | None = "0007_node_output_chunks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "node_execution_logs"
_INDEX = "ix_node_execution_logs_agent_started"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["agent_id", "started_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _INDEX in {ix["name"] for ix in inspector.get_indexes(_TABLE)}:
        op.drop_index(_INDEX, table_name=_TABLE)
