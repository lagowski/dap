"""Audit log helpers (#299, sub-A4b).

Two flavours of the same helper — sync for resource routes (which use
``Session`` from ``persistence/db.py``) and async for fastapi-users
hooks (UserManager.on_after_login etc., where the session lives in the
auth subsystem). Both append a row to ``audit_log`` without committing
— the caller's transaction boundary owns the commit so the audit row
either lands together with the action it describes or rolls back with
it.

The ``event_type`` parameter is typed as :data:`AuditEventType` (a
:data:`typing.Literal`) — see :mod:`dap_engine.auth.audit_event_types`
for the canonical list. mypy will reject any typo or undeclared key
at call sites, killing the class of bugs where a stray
``"user.loggedin"`` writes audit rows that never match a filter.
Adding a new event-type requires a one-line edit to
``audit_event_types.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from dap_engine.auth.audit_event_types import AuditEventType
from dap_engine.persistence.models import AuditLogORM

__all__ = [
    "AuditEventType",
    "record_audit_event",
    "record_audit_event_async",
]


def record_audit_event(
    session: Session,
    *,
    user_id: uuid.UUID | None,
    event_type: AuditEventType,
    event_data: dict[str, Any] | None = None,
) -> None:
    """Append an audit row using the sync session.

    The row is added to the session but not committed — the caller's
    request-scoped transaction (``api/deps.get_session``) commits on
    success / rolls back on error. That ties the audit entry to the
    action it describes: an agent-create that crashes after writing
    its own row will roll back the audit row too, keeping the trail
    consistent with the actual data state.
    """
    session.add(
        AuditLogORM(
            user_id=user_id,
            event_type=event_type,
            event_data=event_data,
        )
    )


async def record_audit_event_async(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    event_type: AuditEventType,
    event_data: dict[str, Any] | None = None,
) -> None:
    """Async counterpart for fastapi-users hooks (and any other async caller).

    Commits eagerly. fastapi-users invokes its ``on_after_*`` hooks
    *after* the action's transaction has already committed, so the
    session passed in is no longer holding an open tx — adding to it
    without a commit would leave the audit row orphaned in the unit
    of work and lost on session close. The eager commit here is
    self-contained: only the audit row is in flight, so it's
    semantically equivalent to a separate audit-log session.
    """
    session.add(
        AuditLogORM(
            user_id=user_id,
            event_type=event_type,
            event_data=event_data,
        )
    )
    await session.commit()
