"""UserManager + auth backends + FastAPIUsers instance.

The JWT secret is configured once from the engine lifespan via
``configure_jwt`` and lives on a module-level holder; ``get_jwt_strategy``
reads it on every login. ``UserManager`` instances pull the same value
into ``reset_password_token_secret`` and ``verification_token_secret``
on construction so fastapi-users' password-reset / verification routers
work end-to-end without per-request indirection.

Two authentication backends are wired into the ``FastAPIUsers`` instance:
- ``auth_backend`` — JWT bearer (``/auth/jwt/login`` flow)
- ``api_token_backend`` — opaque ``dap_*`` tokens (``/auth/api-tokens``,
  see ``api_tokens.py``)

Both share the standard ``Authorization: Bearer <token>`` header.
``current_user(active=True)`` accepts whichever backend reads the token
successfully — JWT is tried first because its decode is purely
in-process (no DB hit) for non-API-token requests.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import Depends, Request, Response
from fastapi_users import (
    BaseUserManager,
    FastAPIUsers,
    InvalidPasswordException,
    UUIDIDMixin,
)
from fastapi_users import schemas as fa_users_schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
    Strategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from dap_engine.auth.api_tokens import looks_like_api_token, verify_api_token
from dap_engine.auth.audit import record_audit_event_async
from dap_engine.auth.db import get_async_session, get_user_db
from dap_engine.persistence.models import UserORM

# 15 minutes by default — short enough that revocation latency stays
# bounded even without a token blacklist (we don't ship one yet).
# Override via ``DAP_AUTH_ACCESS_TTL`` env var → ``EngineConfig``.
DEFAULT_ACCESS_TTL_SECONDS = 60 * 15

# Server-side password policy — must stay aligned with the dashboard's
# Zod rule (see ``apps/dashboard/src/app/(auth)/signup/page.tsx``).
MIN_PASSWORD_LENGTH = 8


# In-process holder for the JWT secret. The engine lifespan writes here
# at startup; ``get_jwt_strategy`` reads it on every login. A module-level
# attribute (not app.state) mirrors fastapi-users' expectation that the
# strategy factory takes no arguments.
_JWT_SECRET: str | None = None
_ACCESS_TTL_SECONDS: int = DEFAULT_ACCESS_TTL_SECONDS
_LOG_RESET_TOKENS: bool = False


def configure_jwt(
    secret: str,
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
    *,
    log_reset_tokens: bool = False,
) -> None:
    """Set the JWT secret + lifetime — called once from the engine lifespan.

    Empty secret raises immediately so misconfig fails on startup, not on
    the first login attempt.

    ``log_reset_tokens`` is a developer convenience for self-hosted
    setups without email delivery. **Off by default** because reset
    tokens are credentials — leaking them into log aggregation would
    hand attackers account-takeover material. See ``EngineConfig.
    auth_log_reset_tokens`` for the public knob.
    """
    if not secret:
        raise ValueError("DAP_AUTH_JWT_SECRET must be a non-empty string")
    global _JWT_SECRET, _ACCESS_TTL_SECONDS, _LOG_RESET_TOKENS  # noqa: PLW0603 — module-level by design
    _JWT_SECRET = secret
    _ACCESS_TTL_SECONDS = access_ttl_seconds
    _LOG_RESET_TOKENS = log_reset_tokens


def _resolve_jwt_secret_or_raise() -> str:
    if _JWT_SECRET is None:
        raise RuntimeError(
            "JWT secret not configured — call dap_engine.auth.users.configure_jwt() "
            "from the engine lifespan before serving auth routes"
        )
    return _JWT_SECRET


class UserManager(UUIDIDMixin, BaseUserManager[UserORM, uuid.UUID]):
    """Hooks for register / login / password-reset events.

    Reads the configured JWT secret on construction so password-reset
    and verification flows (Phase E) sign their tokens with the same
    key the JWT login uses. UserManager instances are short-lived
    (one per request via the ``get_user_manager`` dependency), so this
    pulls the current secret value cleanly without a class-level cache.
    """

    def __init__(
        self,
        user_db: SQLAlchemyUserDatabase[UserORM, uuid.UUID],
    ) -> None:
        super().__init__(user_db)
        secret = _resolve_jwt_secret_or_raise()
        self.reset_password_token_secret = secret
        self.verification_token_secret = secret

    async def validate_password(
        self,
        password: str,
        user: fa_users_schemas.BaseUserCreate | UserORM,
    ) -> None:
        """Server-side password policy — runs on register, reset, and
        admin-set paths. Matches the dashboard's Zod rule (min 8 chars)
        so the policy is the same regardless of which client wrote
        the field (#300, sub-B3).

        Keeps the validator deliberately minimal: complexity rules
        (mixed-case, digits) tend to push users toward predictable
        substitutions; length is the cheap and effective gate.
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                reason=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            )

    async def on_after_register(
        self,
        user: UserORM,
        request: Request | None = None,
    ) -> None:
        logging.getLogger("dap.engine.auth").info(
            "user.registered",
            extra={"user_id": str(user.id), "email": user.email},
        )
        await record_audit_event_async(
            self.user_db.session,  # type: ignore[attr-defined]
            user_id=user.id,
            event_type="user.registered",
            event_data={"email": user.email},
        )

    async def on_after_login(
        self,
        user: UserORM,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        logging.getLogger("dap.engine.auth").info(
            "user.logged_in",
            extra={"user_id": str(user.id)},
        )
        await record_audit_event_async(
            self.user_db.session,  # type: ignore[attr-defined]
            user_id=user.id,
            event_type="user.logged_in",
            event_data={"email": user.email},
        )

    async def on_after_forgot_password(
        self,
        user: UserORM,
        token: str,
        request: Request | None = None,
    ) -> None:
        """Forgot-password hook (#300, sub-B3).

        v0.3 doesn't have email delivery yet — Phase E ships that.
        For self-hosted setups without email, an operator can opt in
        to **token logging** via ``DAP_AUTH_LOG_RESET_TOKENS=1`` and
        copy the token out of stdout. **Default is off** because
        reset tokens are credentials; production log aggregation
        would otherwise routinely contain account-takeover material.
        The audit log always captures the *event* (no token) so
        admins can observe reset attempts.

        fastapi-users only invokes this hook when a user matches the
        submitted email — unknown emails get a 202 from the endpoint
        without firing the hook (anti-enumeration is enforced
        upstream, not here).
        """
        logger = logging.getLogger("dap.engine.auth")
        if _LOG_RESET_TOKENS:
            logger.warning(
                "user.forgot_password (token issued, email delivery TODO): "
                "user_id=%s reset_token=%s",
                user.id,
                token,
            )
        else:
            logger.info(
                "user.forgot_password (token issued, not logged — "
                "set DAP_AUTH_LOG_RESET_TOKENS=1 for dev): user_id=%s",
                user.id,
            )
        await record_audit_event_async(
            self.user_db.session,  # type: ignore[attr-defined]
            user_id=user.id,
            event_type="user.forgot_password",
            event_data={"email": user.email},
        )

    async def on_after_reset_password(
        self,
        user: UserORM,
        request: Request | None = None,
    ) -> None:
        """Audit successful password resets — no token in the event data
        (token already burned by the time this fires)."""
        logging.getLogger("dap.engine.auth").info(
            "user.password_reset", extra={"user_id": str(user.id)}
        )
        await record_audit_event_async(
            self.user_db.session,  # type: ignore[attr-defined]
            user_id=user.id,
            event_type="user.password_reset",
            event_data={"email": user.email},
        )

    async def delete(
        self,
        user: UserORM,
        request: Request | None = None,
    ) -> None:
        """Soft-delete the user: stamp ``deleted_at`` + flip ``is_active``.

        fastapi-users' default ``delete()`` calls ``user_db.delete(user)``
        which is a hard ``DELETE FROM users``. We override so the row stays
        in the table — that keeps any FK referencing this user (introduced
        in a follow-up sub-PR for ``agents`` / ``pipelines`` / etc.) valid
        without cascading data loss. ``is_active=False`` makes the
        ``current_user(active=True)`` dependency reject any future request
        from this user, so soft-deleted accounts can no longer authenticate
        even if they hold a valid JWT.

        Hard delete (audit-driven, admin-initiated, GDPR right-to-erasure)
        lands as a separate admin endpoint in Phase C/E.
        """
        await self.on_before_delete(user, request)
        await self.user_db.update(
            user,
            {"deleted_at": datetime.now(UTC), "is_active": False},
        )
        await record_audit_event_async(
            self.user_db.session,  # type: ignore[attr-defined]
            user_id=user.id,
            event_type="user.deleted",
            event_data={"email": user.email, "soft": True},
        )
        await self.on_after_delete(user, request)


# ---------------------------------------------------------------------------
# JWT strategy + auth backend
# ---------------------------------------------------------------------------


def get_jwt_strategy() -> JWTStrategy[UserORM, uuid.UUID]:
    return JWTStrategy(
        secret=_resolve_jwt_secret_or_raise(),
        lifetime_seconds=_ACCESS_TTL_SECONDS,
    )


_bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=_bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ---------------------------------------------------------------------------
# API token strategy — long-lived tokens for CLI / scripts (sub-A3).
# ---------------------------------------------------------------------------


class ApiTokenStrategy(Strategy[UserORM, uuid.UUID]):
    """fastapi-users Strategy that resolves ``dap_*`` opaque tokens.

    The strategy receives the bearer token from ``BearerTransport``; if
    the value doesn't start with the API-token prefix it returns
    ``None`` (lets the JWT strategy try). Otherwise it does the DB
    lookup defined in ``api_tokens.verify_api_token``.

    ``write_token`` raises — API tokens are created via the dedicated
    ``POST /auth/api-tokens`` endpoint, not via the standard login
    flow. ``destroy_token`` is a no-op for the same reason: revocation
    goes through ``DELETE /auth/api-tokens/{id}``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read_token(
        self,
        token: str | None,
        user_manager: BaseUserManager[UserORM, uuid.UUID],
    ) -> UserORM | None:
        if not token or not looks_like_api_token(token):
            return None
        result = await verify_api_token(token, self.session)
        if result is None:
            return None
        user, _ = result
        return user

    async def write_token(self, user: UserORM) -> str:
        raise NotImplementedError(
            "API tokens are minted via POST /auth/api-tokens, not the standard login flow"
        )

    async def destroy_token(self, token: str, user: UserORM) -> None:
        # Revocation goes through DELETE /auth/api-tokens/{id}; this hook
        # exists only because the AuthenticationBackend interface requires it.
        return None


def get_api_token_strategy(
    session: AsyncSession = Depends(get_async_session),
) -> ApiTokenStrategy:
    return ApiTokenStrategy(session)


api_token_backend = AuthenticationBackend(
    name="api-token",
    # Same Authorization: Bearer header as JWT — strategy disambiguates
    # by token shape ("dap_*" vs JWT). tokenUrl is OpenAPI metadata only.
    transport=BearerTransport(tokenUrl="auth/api-tokens"),
    get_strategy=get_api_token_strategy,
)


# ---------------------------------------------------------------------------
# UserManager dependency + FastAPIUsers instance
# ---------------------------------------------------------------------------


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[UserORM, uuid.UUID] = Depends(get_user_db),
) -> AsyncGenerator[UserManager]:
    yield UserManager(user_db)


fastapi_users: FastAPIUsers[UserORM, uuid.UUID] = FastAPIUsers[UserORM, uuid.UUID](
    get_user_manager,
    [auth_backend, api_token_backend],
)


# Convenience dependency for protected routes — kept here so callers
# don't need to know about the underlying ``fastapi_users.current_user``
# factory. ``active=True`` rejects users where ``is_active=False`` —
# which includes soft-deleted users (``UserManager.delete`` flips the
# flag) and admin-suspended users (Phase C will add suspend/unsuspend
# endpoints that toggle the same column).
#
# Accepts both JWT bearer tokens (regular dashboard sessions) AND
# ``dap_*`` API tokens (CLI / scripts) — see the multi-backend
# ``FastAPIUsers`` instance above.
current_active_user = fastapi_users.current_user(active=True)


async def _jwt_only_backends(
    request: Request,
) -> list[AuthenticationBackend[UserORM, uuid.UUID]]:
    """Restrict an endpoint to JWT auth — used by API-token CRUD itself.

    Issuing or revoking an API token via *another* API token would let
    a leaked CLI credential silently mint long-lived siblings (or
    revoke the user's other tokens). Forcing JWT here means the
    operator must have an active password / OAuth session — same trust
    boundary as managing the user's profile.

    The signature mirrors what fastapi-users' ``get_enabled_backends``
    expects (async callable accepting ``Request``), even though we
    don't dispatch on the request — the contract is what matters.
    """
    return [auth_backend]


current_active_user_jwt_only = fastapi_users.current_user(
    active=True,
    get_enabled_backends=_jwt_only_backends,
)
