"""Authentication and audit ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from dap_engine.persistence.model_base import Base


class UserORM(SQLAlchemyBaseUserTableUUID, Base):
    """Authenticated user.

    Inherits the ``id`` (UUID), ``email`` (unique), ``hashed_password``,
    ``is_active``, ``is_superuser``, ``is_verified`` columns from
    ``SQLAlchemyBaseUserTableUUID``. We extend with audit timestamps and
    a soft-delete column.

    Naming note: the fastapi-users default ``__tablename__`` is ``user``
    (singular). We override to ``users`` to match the rest of the schema
    (``agents``, ``pipelines``, ``runs``, ``projects``, …).

    Soft-delete: the ``UserManager.delete`` override (in
    ``auth/users.py``) writes ``deleted_at`` + flips ``is_active=False``
    instead of issuing ``DELETE FROM users``. This keeps the row alive
    so any future FK pointing at the user — e.g. the ``user_id`` column
    that lands on ``agents`` / ``pipelines`` / ``projects`` / ``runs`` in
    a follow-up sub-PR — remains valid without cascading data loss.
    Hard delete (admin-initiated, GDPR right-to-erasure) is exposed
    separately in Phase C/E.
    """

    __tablename__ = "users"

    # `default` runs Python-side on ORM `User(...)` construction (which is
    # how fastapi-users creates rows). `onupdate` keeps `updated_at` fresh
    # on subsequent UPDATEs without callers having to remember.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Linked OAuth identities. Eager-loaded ("joined") because fastapi-users'
    # SQLAlchemyUserDatabase reads this list on every login lookup; lazy
    # loading would emit a separate SELECT per request.
    oauth_accounts: Mapped[list[OAuthAccountORM]] = relationship(
        "OAuthAccountORM",
        lazy="joined",
        cascade="all, delete-orphan",
    )


class ApiTokenORM(Base):
    """Long-lived API token for CLI / scripts (#299, sub-A3).

    Tokens are opaque random strings (``dap_<43-char-base64>``) issued
    via ``POST /auth/api-tokens``. The raw token is shown to the caller
    exactly once at creation time; the database stores only the SHA-256
    hash and the first 8 chars (``token_prefix``) for indexed lookup.

    Why SHA-256 instead of bcrypt/argon2: the token has 256 bits of
    entropy, so brute-force is infeasible regardless of hash speed; a
    cryptographic but cheap hash keeps every authenticated request
    inexpensive. (Password hashes use slow KDFs because passwords have
    far less entropy and offline cracking is the realistic threat —
    that calculus doesn't apply here.)

    Lifecycle:
    - ``revoked_at`` set on ``DELETE /auth/api-tokens/{id}`` — the
      verify path treats any non-NULL value as "rejected"
    - ``expires_at`` optional. NULL means "no expiry"; a past timestamp
      is treated as revoked
    - ``last_used_at`` is touched on every successful auth so admins
      can spot stale tokens; not on the hot path under a tx (we update
      it best-effort and tolerate races)
    """

    __tablename__ = "api_tokens"
    __table_args__ = (
        # Lookup happens on every authenticated CLI call: select by
        # prefix (cheap, indexed), then SHA-256 verify the candidates.
        # Almost always one match per prefix; the verify step exists
        # purely to defeat collisions (very unlikely with 8 chars from a 64-
        # symbol alphabet) and to be timing-attack-resistant.
        Index("ix_api_tokens_token_prefix", "token_prefix"),
        Index("ix_api_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=False,
    )
    # Human-readable label set by the user at create time
    # (e.g. "ci-pipeline", "personal-laptop").
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogORM(Base):
    """Append-only audit trail of security-relevant events (#299, sub-A4).

    Captures the *what* + *who* + *when* of operator-visible auth /
    ownership events. ``event_type`` is a stable string key (e.g.
    ``user.registered``, ``user.logged_in``, ``api_token.created``,
    ``api_token.revoked``); ``event_data`` is a free-form JSON
    payload for per-event details (target user id, token prefix, IP
    address from the request, …).

    Lifecycle:
    - **Append-only**: rows are never updated or deleted by application
      code. A retention policy (e.g. monthly partition pruning) can
      reclaim space later if growth becomes a concern (Phase E).
    - ``user_id`` is nullable so events that happen *before* the actor
      is identified (e.g. failed login by an unknown email) can still
      be recorded.
    - No FK to ``users.id`` either — the row should survive even if
      the user account is hard-deleted (audit integrity > referential
      cleanliness).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        # Admin filter UI lists by user, by event type, or by both. The
        # composite leads with user_id since "show me what user X did"
        # is the primary investigation flow; event_type alone falls back
        # to a small filtered scan.
        Index("ix_audit_log_user_event", "user_id", "event_type"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # Nullable: pre-auth events (failed login by unknown email) and
    # system events (migration, scheduled cleanup) have no actor.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # JSONB on PostgreSQL — supports native JSON path queries (the admin
    # panel will filter by ``event_data->>'token_id'`` etc.) and matches
    # the migration's CREATE TABLE column type. ``JSON.with_variant(JSONB,
    # "postgresql")`` keeps the SQLite path on plain JSON (TEXT under the
    # hood, no native operators but the column type is dialect-aware).
    event_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class OAuthAccountORM(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """Linked OAuth identity (GitHub, Google, …).

    Inherits id (UUID), oauth_name, access_token, expires_at,
    refresh_token, account_id, account_email columns from
    SQLAlchemyBaseOAuthAccountTableUUID.

    ``user_id`` is overridden because the parent helper hardcodes
    ``ForeignKey("user.id", …)`` (singular table name) but our user
    table is ``users`` (plural — see UserORM.__tablename__). Without
    the override, SQLAlchemy raises NoReferencedTableError on import.

    Indexes are declared at the ORM level so ``Base.metadata.create_all``
    creates them on fresh DBs (it runs *before* ``apply_migrations`` —
    see ``persistence/db.py``). The migration provides idempotent
    fallbacks for existing DBs that predate this release.
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # Every callback resolves an existing OAuth identity by
        # ``(oauth_name, account_id)``; without this composite the lookup
        # is a full table scan on every login (and login frequency
        # scales with active-user count).
        Index("ix_oauth_accounts_provider_account", "oauth_name", "account_id"),
    )

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            GUID,
            ForeignKey("users.id", ondelete="cascade"),
            nullable=False,
            # User-scoped queries (list / unlink an OAuth account from
            # the admin panel in Phase C) hit this column on every page;
            # an explicit single-column index keeps those fast even when
            # the composite above doesn't help (it leads with oauth_name).
            index=True,
        )
