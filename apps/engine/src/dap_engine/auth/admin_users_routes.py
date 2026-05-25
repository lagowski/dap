"""Admin-only user listing (#301, sub-C2).

fastapi-users' ``get_users_router`` exposes ``GET/PATCH/DELETE
/users/{id}`` and ``GET/PATCH /users/me`` but **not** a list endpoint.
The admin panel needs one to render its user table, so this module
adds it.

Per-user mutations (suspend, change role, soft-delete) reuse the
fastapi-users routes — they already enforce ``is_superuser`` on
non-self updates and our ``UserManager.delete`` override gives them
the soft-delete behaviour we want.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from dap_engine.auth.db import get_async_session
from dap_engine.auth.users import require_admin_user
from dap_engine.persistence.models import UserORM

router = APIRouter(prefix="/users", tags=["users"])


class AdminUserRead(BaseModel):
    """Public representation in the admin list.

    Wider than fastapi-users' ``UserRead`` — exposes ``created_at`` /
    ``last_login_at`` / ``deleted_at`` so the admin table can show
    "joined", "last active", and the soft-delete state at a glance.
    The schema is intentionally not aliased to ``UserRead`` because
    these fields are admin-only — surfacing ``deleted_at`` on the
    public read endpoint would leak the soft-delete state to the
    affected user.
    """

    id: uuid.UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    created_at: datetime | None
    last_login_at: datetime | None
    deleted_at: datetime | None

    model_config = {"from_attributes": True}


class AdminUserList(BaseModel):
    """Paginated list response — matches the shape the dashboard uses
    for other admin tables (``items`` + ``total`` + ``offset`` +
    ``limit``)."""

    items: list[AdminUserRead]
    total: int
    offset: int
    limit: int
    has_more: bool


@router.get(
    "",
    response_model=AdminUserList,
    summary="List all users (admin-only)",
    dependencies=[Depends(require_admin_user)],
)
async def list_users(
    session: AsyncSession = Depends(get_async_session),
    *,
    include_deleted: bool = Query(
        default=False,
        description=(
            "Include soft-deleted users (``deleted_at`` is not NULL). "
            "Default false so the table mirrors the engine's notion of "
            "active membership."
        ),
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> AdminUserList:
    """Paginated user list.

    Admin-only via the route-level ``require_admin_user`` dep — non-
    admins get ``404`` (anti-enumeration, matching the sub-A4b2
    resource routes); anonymous → ``401`` from the underlying
    ``current_active_user`` dep.
    """
    where = [] if include_deleted else [UserORM.deleted_at.is_(None)]
    total = (
        await session.scalar(
            select(func.count()).select_from(UserORM).where(*where),
        )
    ) or 0
    # ``noload(UserORM.oauth_accounts)`` overrides the model's
    # ``lazy="joined"`` for this query specifically. Without it the
    # OUTER JOIN would duplicate user rows per linked OAuth account,
    # and ``offset`` / ``limit`` applied *before* ``.unique()`` would
    # skip users or return fewer than ``limit`` — broken pagination
    # (Copilot review on PR #328). The list view doesn't render
    # oauth_accounts, so dropping them costs nothing.
    rows = (
        (
            await session.execute(
                select(UserORM)
                .options(noload(UserORM.oauth_accounts))
                .where(*where)
                .order_by(UserORM.created_at.desc())
                .offset(offset)
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )

    return AdminUserList(
        items=[AdminUserRead.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + limit < total,
    )
