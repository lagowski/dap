"""UserManager + JWT authentication backend + FastAPIUsers instance.

The JWT secret is configured once from the engine lifespan via
``configure_jwt`` and lives on a module-level holder; ``get_jwt_strategy``
reads it on every login. ``UserManager`` instances pull the same value
into ``reset_password_token_secret`` and ``verification_token_secret``
on construction so fastapi-users' password-reset / verification routers
work end-to-end without per-request indirection.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from dap_engine.auth.db import get_user_db
from dap_engine.persistence.models import UserORM

# 15 minutes by default — short enough that revocation latency stays
# bounded even without a token blacklist (we don't ship one yet).
# Override via ``DAP_AUTH_ACCESS_TTL`` env var → ``EngineConfig``.
DEFAULT_ACCESS_TTL_SECONDS = 60 * 15


# In-process holder for the JWT secret. The engine lifespan writes here
# at startup; ``get_jwt_strategy`` reads it on every login. A module-level
# attribute (not app.state) mirrors fastapi-users' expectation that the
# strategy factory takes no arguments.
_JWT_SECRET: str | None = None
_ACCESS_TTL_SECONDS: int = DEFAULT_ACCESS_TTL_SECONDS


def configure_jwt(secret: str, access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS) -> None:
    """Set the JWT secret + lifetime — called once from the engine lifespan.

    Empty secret raises immediately so misconfig fails on startup, not on
    the first login attempt.
    """
    if not secret:
        raise ValueError("DAP_AUTH_JWT_SECRET must be a non-empty string")
    global _JWT_SECRET, _ACCESS_TTL_SECONDS  # noqa: PLW0603 — module-level by design
    _JWT_SECRET = secret
    _ACCESS_TTL_SECONDS = access_ttl_seconds


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

    async def on_after_register(
        self,
        user: UserORM,
        request: Request | None = None,
    ) -> None:
        # Phase A only logs; structured audit log lands later in #299.
        logging.getLogger("dap.engine.auth").info(
            "user.registered",
            extra={"user_id": str(user.id), "email": user.email},
        )


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
# UserManager dependency + FastAPIUsers instance
# ---------------------------------------------------------------------------


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[UserORM, uuid.UUID] = Depends(get_user_db),
) -> AsyncGenerator[UserManager]:
    yield UserManager(user_db)


fastapi_users: FastAPIUsers[UserORM, uuid.UUID] = FastAPIUsers[UserORM, uuid.UUID](
    get_user_manager,
    [auth_backend],
)


# Convenience dependency for protected routes — kept here so callers
# don't need to know about the underlying ``fastapi_users.current_user``
# factory. ``active=True`` excludes suspended / soft-deleted users.
current_active_user = fastapi_users.current_user(active=True)
