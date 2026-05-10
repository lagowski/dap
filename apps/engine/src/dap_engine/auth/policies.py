"""Ownership / RBAC policies (#299, sub-A4b).

Generic helpers used by every resource route that needs to gate access
on "owner OR admin". Centralised here so the rule has one place to
evolve (e.g. adding team membership in the future) instead of each
router rolling its own check.

Convention: when a check fails, raise ``HTTPException(404)`` — not
``403``. A 403 leaks the existence of an out-of-scope resource ("yes,
this id exists, but you can't see it"); a 404 is indistinguishable
from "no such id" so the endpoint can't be used to enumerate other
users' resources. The same anti-enumeration rule we already apply on
``DELETE /auth/api-tokens/{id}`` (sub-A3).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from dap_engine.persistence.models import UserORM

__all__ = [
    "is_owner_or_admin",
    "verify_owner_or_admin",
]


def is_owner_or_admin(user: UserORM, resource_user_id: uuid.UUID | None) -> bool:
    """Pure predicate — admins see everything, owners see their own.

    A NULL ``resource_user_id`` (legacy pre-v0.3 row that backfill
    didn't claim, or a row created before sub-A4b enforces NOT NULL
    in the application layer) is visible to admins only. Non-admins
    see those rows as "not yours" so they don't get exposed by
    accident during the v0.3 transition.
    """
    if user.is_superuser:
        return True
    if resource_user_id is None:
        return False
    return resource_user_id == user.id


def verify_owner_or_admin(
    user: UserORM,
    resource_user_id: uuid.UUID | None,
    *,
    resource_kind: str = "resource",
) -> None:
    """Raise ``HTTPException(404)`` when the caller can't access the row.

    Use immediately after the ``GET ... WHERE id = ?`` lookup that
    returned the row. ``resource_kind`` is the noun used in the
    detail message (``"agent"``, ``"pipeline"``, …) so the response
    body matches what the user just tried to access.
    """
    if is_owner_or_admin(user, resource_user_id):
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource_kind} not found",
    )
