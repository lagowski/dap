"""pipeline_versions.backend_profiles column — pipeline-export/2 (#478).

Revision ID: 0003_pipeline_versions_backend_profiles
Revises: 0002_batch_runs
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_pipeline_versions_backend_profiles"
down_revision: str | None = "0002_batch_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("pipeline_versions")}
    if "backend_profiles" not in cols:
        op.add_column(
            "pipeline_versions",
            sa.Column("backend_profiles", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("pipeline_versions")}
    if "backend_profiles" in cols:
        op.drop_column("pipeline_versions", "backend_profiles")
