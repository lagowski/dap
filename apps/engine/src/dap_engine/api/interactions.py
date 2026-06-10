"""Admin browse API for the EU AI Act interaction log (#722).

``GET /interactions`` — newest-first, paginated, admin-only (the
route-level ``require_admin_user`` dep 404s for non-admins, same
anti-enumeration shape as ``/audit`` and ``/admin``). Content returned
here was redacted at write time; this endpoint never touches raw text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_session
from dap_engine.auth.users import require_admin_user
from dap_engine.persistence.interaction_log import list_interactions

router = APIRouter(prefix="/interactions", tags=["interactions"])


class InteractionRead(BaseModel):
    """Public representation of one redacted interaction record."""

    id: uuid.UUID
    user_id: uuid.UUID | None
    created_at: datetime
    surface: str
    provider: str | None
    model: str | None
    redacted_request: list[dict[str, Any]]
    redacted_response: str
    tokens_used: int | None
    grounded: bool | None
    extra: dict[str, Any] | None

    model_config = {"from_attributes": True}


class InteractionList(BaseModel):
    """Paginated list — same envelope as ``/audit/events``."""

    items: list[InteractionRead]
    total: int
    offset: int
    limit: int


@router.get(
    "",
    response_model=InteractionList,
    summary="List interaction-log records (admin-only, paginated)",
    dependencies=[Depends(require_admin_user)],
)
def list_interaction_records(
    session: Session = Depends(get_session),
    *,
    surface: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> InteractionList:
    rows, total = list_interactions(
        session,
        offset=offset,
        limit=limit,
        surface=surface,
        user_id=user_id,
        created_from=created_from,
        created_to=created_to,
    )
    return InteractionList(
        items=[InteractionRead.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
