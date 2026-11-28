"""runs.gate_expires_at column — gate approval deadline (#582).

Revision ID: 0005_runs_gate_expires_at
Revises: 0004_project_auto_approve_nodes
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_runs_gate_expires_at"
down_revision: str | None = "0004_project_auto_approve_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("runs")}
    if "gate_expires_at" not in cols:
        op.add_column(
            "runs",
            sa.Column("gate_expires_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("runs")}
    if "gate_expires_at" in cols:
        op.drop_column("runs", "gate_expires_at")
