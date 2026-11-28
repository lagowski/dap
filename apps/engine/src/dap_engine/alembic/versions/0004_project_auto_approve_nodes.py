"""projects.auto_approve_nodes column — project-level gate auto-approval (#477).

Revision ID: 0004_project_auto_approve_nodes
Revises: 0003_pipeline_versions_backend_profiles
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_project_auto_approve_nodes"
down_revision: str | None = "0003_pipeline_versions_backend_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "auto_approve_nodes" not in cols:
        op.add_column(
            "projects",
            sa.Column("auto_approve_nodes", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "auto_approve_nodes" in cols:
        op.drop_column("projects", "auto_approve_nodes")
