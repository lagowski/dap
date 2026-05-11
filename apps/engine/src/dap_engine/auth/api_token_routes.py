"""CRUD endpoints for API tokens (#299, sub-A3).

All routes require **JWT** auth (``current_active_user_jwt_only``) —
not API-token auth. Letting an API token mint or revoke other API
tokens would mean a single leaked CLI credential could silently
escalate (creating siblings without a TTL) or sabotage the user
(revoking their other tokens). Forcing JWT here means the operator
must have an active password / OAuth session, which gates the action
to a "real" login event.

The raw token value is returned exactly once — at creation. The DB
stores only its SHA-256 hash and the indexed ``token_prefix`` (see
``api_tokens.py`` and ``ApiTokenORM`` for the rationale).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from dap_engine.auth.api_tokens import generate_token
from dap_engine.auth.audit import record_audit_event_async
from dap_engine.auth.db import get_async_session
from dap_engine.auth.users import current_active_user_jwt_only
from dap_engine.persistence.models import ApiTokenORM, UserORM

router = APIRouter(prefix="/auth/api-tokens", tags=["auth"])


class ApiTokenCreateRequest(BaseModel):
    """Payload for ``POST /auth/api-tokens``."""

    name: str = Field(min_length=1, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiTokenRead(BaseModel):
    """Public representation in list responses — no raw token here."""

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ApiTokenCreateResponse(ApiTokenRead):
    """Returned only by the create endpoint — includes the raw value."""

    token: str
    """Raw ``dap_<...>`` token. Shown ONCE, not retrievable later."""


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_token(
    payload: ApiTokenCreateRequest,
    user: UserORM = Depends(current_active_user_jwt_only),
    session: AsyncSession = Depends(get_async_session),
) -> ApiTokenCreateResponse:
    """Mint a new API token for the authenticated user.

    The raw token is returned in the response body and never persisted;
    callers must capture it on receipt or generate a new one.
    """
    generated = generate_token()
    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    row = ApiTokenORM(
        user_id=user.id,
        name=payload.name,
        token_prefix=generated.prefix,
        token_hash=generated.sha256_hash,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()  # populate row.id before audit refers to it
    await record_audit_event_async(
        session,
        user_id=user.id,
        event_type="api_token.created",
        event_data={
            "token_id": str(row.id),
            "name": row.name,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    await session.commit()
    await session.refresh(row)

    return ApiTokenCreateResponse(
        id=row.id,
        name=row.name,
        prefix=row.token_prefix,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        token=generated.raw,
    )


@router.get("", response_model=list[ApiTokenRead])
async def list_api_tokens(
    user: UserORM = Depends(current_active_user_jwt_only),
    session: AsyncSession = Depends(get_async_session),
) -> list[ApiTokenRead]:
    """List the caller's tokens.

    Returns metadata only — never the raw value (which exists only in
    the create response and the user's clipboard).
    """
    stmt = (
        select(ApiTokenORM)
        .where(ApiTokenORM.user_id == user.id)
        .order_by(ApiTokenORM.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        ApiTokenRead(
            id=r.id,
            name=r.name,
            prefix=r.token_prefix,
            created_at=r.created_at,
            expires_at=r.expires_at,
            last_used_at=r.last_used_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: uuid.UUID,
    user: UserORM = Depends(current_active_user_jwt_only),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Soft-revoke a token (sets ``revoked_at`` to now).

    Returns 404 when the token doesn't exist OR belongs to another
    user — the two responses are deliberately indistinguishable so the
    endpoint can't be used to enumerate other users' token IDs.
    """
    stmt = select(ApiTokenORM).where(
        ApiTokenORM.id == token_id,
        ApiTokenORM.user_id == user.id,
    )
    row = (await session.execute(stmt)).scalars().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await record_audit_event_async(
            session,
            user_id=user.id,
            event_type="api_token.revoked",
            event_data={"token_id": str(row.id), "name": row.name},
        )
        # record_audit_event_async commits — second commit is a no-op
        # but harmless; left in for defensive symmetry with the
        # pre-audit shape.
        await session.commit()


# ---------------------------------------------------------------------------
# Admin-wide views (#301, sub-C4)
# ---------------------------------------------------------------------------


class AdminApiTokenRead(ApiTokenRead):
    """Admin list row — same shape as ``ApiTokenRead`` plus owner info.

    Wider than the user-scoped view so the admin can identify whose
    token is whose without a second round-trip. ``owner_email`` lives
    on the wire as a plain string (admin already has the right to
    enumerate users via ``/users``, so no new info disclosure).
    """

    owner_id: uuid.UUID
    owner_email: str


class AdminApiTokenList(BaseModel):
    """Paginated list — same shape as the rest of the admin surface."""

    items: list[AdminApiTokenRead]
    total: int
    offset: int
    limit: int


@router.get(
    "/admin",
    response_model=AdminApiTokenList,
    summary="List every user's API tokens (admin-only)",
)
async def list_all_api_tokens(
    user: UserORM = Depends(current_active_user_jwt_only),
    session: AsyncSession = Depends(get_async_session),
    *,
    include_revoked: bool = Query(
        default=True,
        description=(
            "Include already-revoked tokens. Default true so the admin "
            "can see the full revocation history; flip to false for "
            "only-currently-valid view."
        ),
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> AdminApiTokenList:
    """Paginated list of every user's tokens.

    Admin-only — JWT-protected (same as the user-scoped routes here
    so an API token can't enumerate other API tokens). Non-admin gets
    ``404`` (anti-enumeration, mirrors the rest of the admin surface).
    Anonymous gets ``401`` from the JWT-only dependency.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    where: list[ColumnElement[bool]] = []
    if not include_revoked:
        where.append(ApiTokenORM.revoked_at.is_(None))

    from sqlalchemy import func as sql_func  # noqa: PLC0415 — narrow scope

    total = (
        await session.scalar(
            select(sql_func.count()).select_from(ApiTokenORM).where(*where),
        )
    ) or 0

    # JOIN on owner so we can ship the email in the same response —
    # avoids the dashboard needing a second round-trip per row.
    # ``select(ApiTokenORM, UserORM.email)`` returns ``tuple[ApiTokenORM, str]``
    # rows; SQLAlchemy's generic ``select`` overloads support the
    # mixed-entity shape but mypy needs the explicit annotation below.
    rows = (
        await session.execute(
            select(ApiTokenORM, UserORM.email)  # type: ignore[call-overload]
            .join(UserORM, UserORM.id == ApiTokenORM.user_id)
            .where(*where)
            .order_by(ApiTokenORM.created_at.desc())
            .offset(offset)
            .limit(limit),
        )
    ).all()

    return AdminApiTokenList(
        items=[
            AdminApiTokenRead(
                id=token.id,
                name=token.name,
                prefix=token.token_prefix,
                created_at=token.created_at,
                expires_at=token.expires_at,
                last_used_at=token.last_used_at,
                revoked_at=token.revoked_at,
                owner_id=token.user_id,
                owner_email=email,
            )
            for token, email in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.delete(
    "/admin/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke any user's API token (admin-only)",
)
async def admin_revoke_api_token(
    token_id: uuid.UUID,
    user: UserORM = Depends(current_active_user_jwt_only),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Soft-revoke any token, regardless of owner.

    Audit row carries both the *acting* admin's ``user_id`` and the
    *target* token's owner in ``event_data.target_user_id`` so the
    log clearly distinguishes self-revocations from admin
    interventions.

    Non-admin → ``404`` (anti-enumeration). Anonymous → ``401``.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    stmt = select(ApiTokenORM).where(ApiTokenORM.id == token_id)
    row = (await session.execute(stmt)).scalars().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await record_audit_event_async(
            session,
            # Admin is the acting user — ``user_id`` on the audit row.
            user_id=user.id,
            event_type="api_token.revoked",
            event_data={
                "token_id": str(row.id),
                "name": row.name,
                # Separate field so log filters can group by who *owned*
                # the token vs. who *triggered* the revocation.
                "target_user_id": str(row.user_id),
                "by_admin": True,
            },
        )
        await session.commit()
