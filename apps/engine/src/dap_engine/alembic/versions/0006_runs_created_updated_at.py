"""runs.created_at / runs.updated_at columns — time-based sorting (#606).

Revision ID: 0006_runs_created_updated_at
Revises: e626b70eb11c
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_runs_created_updated_at"
down_revision: str | None = "e626b70eb11c"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("runs")}
    if "created_at" not in cols:
        op.add_column(
            "runs",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        # Back-fill existing rows with started_at so the column is non-null
        # in practice; ORM enforces nullable=False for new rows.
        op.execute("UPDATE runs SET created_at = started_at WHERE created_at IS NULL")
    if "updated_at" not in cols:
        op.add_column(
            "runs",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.execute("UPDATE runs SET updated_at = started_at WHERE updated_at IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("runs")}
    if "created_at" in cols:
        op.drop_column("runs", "created_at")
    if "updated_at" in cols:
        op.drop_column("runs", "updated_at")
