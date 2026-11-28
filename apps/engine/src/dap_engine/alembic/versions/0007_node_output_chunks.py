"""node_output_chunks table — incremental node-output streaming (#662, Phase 3b).

Backs the SSE ``node_log`` event: adapters append one row per stdout/stderr
flush (Phase 3b-2) and the ``/runs/{id}/events`` endpoint pages new rows with
``WHERE run_id = ? AND id > ? ORDER BY id``. ``id`` is the monotonic cursor.

Revision ID: 0007_node_output_chunks
Revises: 0006_runs_created_updated_at
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_node_output_chunks"
down_revision: str | None = "0006_runs_created_updated_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "node_output_chunks" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "node_output_chunks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("node_id", sa.String(), nullable=False),
            sa.Column("execution_id", sa.String(), nullable=True),
            sa.Column("stream", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        # Incremental cursor read: WHERE run_id = ? AND id > ? ORDER BY id.
        op.create_index(
            "ix_node_output_chunks_run_id",
            "node_output_chunks",
            ["run_id", "id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "node_output_chunks" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_node_output_chunks_run_id", table_name="node_output_chunks")
        op.drop_table("node_output_chunks")
