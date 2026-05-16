"""baseline — schema after legacy MIGRATIONS[] runs (audit E6).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-16

This revision is intentionally a no-op. The legacy in-code
``MIGRATIONS[]`` list (see ``dap_engine.persistence.migrations``)
runs FIRST at engine startup and brings any pre-Alembic dev DB
forward to the schema state this baseline represents. Alembic then
takes over for any FUTURE schema changes — those land as new
revisions under this directory via ``alembic revision
--autogenerate`` and chain off ``0001_baseline``.

For a fresh DB the order is:

1. ``Base.metadata.create_all(engine)`` — creates every table
   currently declared in the ORM, including everything the legacy
   migrations would have added.
2. ``apply_migrations(engine)`` — records all 18 legacy migrations
   as applied (the migration bodies are guarded with
   ``_column_exists`` / ``CREATE TABLE IF NOT EXISTS`` so they're
   no-ops on an already-correct schema).
3. ``_apply_alembic_migrations(engine)`` — runs Alembic ``upgrade
   head``, which since this baseline is a no-op just records
   ``0001_baseline`` in ``alembic_version`` and proceeds to any
   later revisions (none yet).

For a pre-Alembic dev DB (already has ``schema_migrations`` table,
no ``alembic_version`` table):

1. ``create_all`` is a no-op for existing tables.
2. ``apply_migrations`` runs any missing legacy migrations.
3. ``_apply_alembic_migrations`` creates ``alembic_version``, stamps
   ``0001_baseline`` as applied (no SQL runs — body is ``pass``),
   then proceeds to any later revisions.

DO NOT add schema operations to this revision. Create a new
revision for changes after baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op. See module docstring for the coexistence contract."""


def downgrade() -> None:
    """No-op. We don't ship down-migrations (same policy as the legacy
    ``MIGRATIONS[]`` list)."""
