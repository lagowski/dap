"""Admin-only audit log read endpoint (#301, sub-C3).

Append-only events land in ``audit_log`` from the auth subsystem and
ownership-enforcing repo helpers. This module exposes them to the
admin panel via a paginated, filterable read endpoint.

Filters are deliberately minimal — ``event_type`` (exact) and
``user_id`` (exact). They cover the two primary investigation flows
the audit log was designed for ("what did user X do?", "show me all
password-reset attempts"); richer filters (date range, JSONB
queries over ``event_data``) can land in a follow-up without
breaking the wire shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dap_engine.auth.db import get_async_session
from dap_engine.auth.users import require_admin_user
from dap_engine.persistence.models import AuditLogORM

router = APIRouter(prefix="/audit", tags=["audit"])


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a prefix filter matches literally.

    ``%`` and ``_`` are SQL LIKE metacharacters; ``\\`` is the escape
    character we pass to ``.like(..., escape="\\")``. Without this, an
    event-type prefix containing ``%`` (or ``_``) would behave as a
    wildcard instead of a literal substring.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AuditEventRead(BaseModel):
    """Public representation of an audit row.

    ``user_id`` and ``event_data`` are both nullable on the column —
    keep the same nullability here so admins can see legitimate
    "no actor" events (migration backfills, pre-auth failures) without
    the API contract lying.
    """

    id: uuid.UUID
    user_id: uuid.UUID | None
    event_type: str
    event_data: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditEventList(BaseModel):
    """Paginated list — matches the shape ``GET /users`` returns."""

    items: list[AuditEventRead]
    total: int
    offset: int
    limit: int


@router.get(
    "/events",
    response_model=AuditEventList,
    summary="List audit events (admin-only, paginated)",
    dependencies=[Depends(require_admin_user)],
)
async def list_audit_events(
    session: AsyncSession = Depends(get_async_session),
    *,
    event_type: str | None = Query(
        default=None,
        description="Filter to events of this exact type (e.g. ``user.logged_in``).",
    ),
    event_type_prefix: str | None = Query(
        default=None,
        description=(
            "Filter to a whole event family by prefix (e.g. ``agent.`` matches "
            "``agent.created`` / ``agent.archived``). LIKE wildcards are escaped."
        ),
    ),
    user_id: uuid.UUID | None = Query(
        default=None,
        description="Filter to events triggered by this user.",
    ),
    created_from: datetime | None = Query(
        default=None,
        description="Only events at or after this timestamp (ISO 8601).",
    ),
    created_to: datetime | None = Query(
        default=None,
        description="Only events at or before this timestamp (ISO 8601).",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> AuditEventList:
    """Paginated audit log read.

    Admin-only via the route-level ``require_admin_user`` dependency —
    non-admins get ``404`` (anti-enumeration); anonymous → ``401``
    from the underlying ``current_active_user`` dep.

    Results are sorted ``created_at DESC`` so the freshest events
    land at the top of the table.
    """
    where: list[ColumnElement[bool]] = []
    if event_type is not None:
        where.append(AuditLogORM.event_type == event_type)
    if event_type_prefix is not None:
        where.append(
            AuditLogORM.event_type.like(f"{_escape_like(event_type_prefix)}%", escape="\\")
        )
    if user_id is not None:
        where.append(AuditLogORM.user_id == user_id)
    if created_from is not None:
        where.append(AuditLogORM.created_at >= created_from)
    if created_to is not None:
        where.append(AuditLogORM.created_at <= created_to)

    total = (
        await session.scalar(
            select(func.count()).select_from(AuditLogORM).where(*where),
        )
    ) or 0
    rows = (
        (
            await session.execute(
                select(AuditLogORM)
                .where(*where)
                .order_by(AuditLogORM.created_at.desc())
                .offset(offset)
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )

    return AuditEventList(
        items=[AuditEventRead.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
