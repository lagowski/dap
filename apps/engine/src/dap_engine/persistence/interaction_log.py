"""Interaction-log persistence — record / list / purge (#722).

Same caller contract as ``auth/audit.py``: ``record_interaction`` adds
the row but does NOT commit — the request handler owns the transaction
boundary, so the interaction record commits (or rolls back) atomically
with everything else the request wrote.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.orm import Session

from dap_engine.persistence.interaction_models import InteractionLogORM

__all__ = ["list_interactions", "purge_interactions", "record_interaction"]


def record_interaction(
    session: Session,
    *,
    user_id: uuid.UUID | None,
    surface: str,
    redacted_request: list[dict[str, Any]],
    redacted_response: str,
    provider: str | None = None,
    model: str | None = None,
    tokens_used: int | None = None,
    grounded: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one redacted interaction. Caller must pass content that has
    already been through :func:`dap_engine.redaction.redact` — this layer
    stores what it is given."""
    session.add(
        InteractionLogORM(
            user_id=user_id,
            surface=surface,
            provider=provider,
            model=model,
            redacted_request=redacted_request,
            redacted_response=redacted_response,
            tokens_used=tokens_used,
            grounded=grounded,
            extra=extra,
        )
    )


def list_interactions(
    session: Session,
    *,
    offset: int,
    limit: int,
    surface: str | None = None,
    user_id: uuid.UUID | None = None,
    created_from: dt.datetime | None = None,
    created_to: dt.datetime | None = None,
) -> tuple[list[InteractionLogORM], int]:
    """Newest-first page of interactions + total count for the filter set."""
    where: list[ColumnElement[bool]] = []
    if surface is not None:
        where.append(InteractionLogORM.surface == surface)
    if user_id is not None:
        where.append(InteractionLogORM.user_id == user_id)
    if created_from is not None:
        where.append(InteractionLogORM.created_at >= created_from)
    if created_to is not None:
        where.append(InteractionLogORM.created_at <= created_to)

    total = session.scalar(select(func.count()).select_from(InteractionLogORM).where(*where)) or 0
    rows = session.scalars(
        select(InteractionLogORM)
        .where(*where)
        .order_by(InteractionLogORM.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), total


def purge_interactions(session: Session, *, older_than: dt.datetime) -> int:
    """Delete interactions created before ``older_than``; return the count.

    Caller commits. Run from the engine-startup retention sweep — see
    ``app.py``'s lifespan.
    """
    result = session.execute(
        delete(InteractionLogORM).where(InteractionLogORM.created_at < older_than)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
