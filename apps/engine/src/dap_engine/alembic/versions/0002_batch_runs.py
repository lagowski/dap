"""batch_runs table — sequential batch pipeline execution (#473).

Revision ID: 0002_batch_runs
Revises: 0001_baseline
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_batch_runs"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guard: create_all() already creates this table on fresh DBs
    # (BatchRunORM is declared in models.py). Skip if it exists.
    bind = op.get_bind()
    if "batch_runs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=True),
        sa.Column("issue_numbers", sa.JSON(), nullable=False),
        sa.Column("stop_on_failure", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("auto_approve", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batch_runs_user_id", "batch_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_runs_user_id", table_name="batch_runs")
    op.drop_table("batch_runs")
