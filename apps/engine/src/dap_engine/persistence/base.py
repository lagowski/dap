"""Shared persistence helpers — ownership filtering + anti-enumeration get (#778 Phase 2).

Before this module, ``_ownership_filter`` existed as four byte-identical
copies in ``agents.py`` / ``pipelines.py`` / ``projects.py`` / ``runs.py``,
and the "get by id, 404 on missing *or* foreign" shape was hand-rolled
per entity. The rules live here once; per-entity modules keep owning
their queries and DTO mapping.

``runs.get_run`` intentionally does NOT use :func:`get_owned_or_not_found`
— it carries an extra defensive PK-mismatch guard (#636) on top of this
shape.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from dap_engine.persistence._common import NotFoundError

__all__ = ["get_owned_or_not_found", "ownership_filter"]


class _OwnedORM(Protocol):
    """Structural type for ORM rows that carry an owner column."""

    user_id: Any  # Mapped[uuid.UUID | None] at runtime


def ownership_filter(
    orm_cls: type[_OwnedORM],
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[ColumnElement[bool]]:
    """Return ``[]`` for admins, else a single-clause filter for the actor.

    Centralised so every list query gets the rule without copy-paste.
    Admins see all rows (including legacy NULL ``user_id`` rows from
    the pre-v0.3 backfill); non-admins only see rows they own.
    """
    if is_admin:
        return []
    return [orm_cls.user_id == actor_id]


def get_owned_or_not_found[OwnedT: _OwnedORM](
    session: Session,
    orm_cls: type[OwnedT],
    entity_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
    label: str,
) -> OwnedT:
    """Fetch a row by PK; raise ``NotFoundError`` on missing OR foreign.

    Anti-enumeration: a foreign id raises with the *same* message as a
    missing id so a non-admin can't probe which ids exist.
    """
    row = session.get(orm_cls, entity_id)
    if row is None or (not is_admin and row.user_id != actor_id):
        raise NotFoundError(f"{label} not found: {entity_id}")
    return row
