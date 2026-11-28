"""Async SQLAlchemy engine for fastapi-users.

The rest of the persistence layer uses a sync engine
(``persistence/db.py``); fastapi-users requires async. This module
maintains a parallel async engine pointing at the same underlying
database. Schema is created/migrated once via the sync engine on
startup; this engine only opens new connections through async drivers.

Driver selection mirrors the sync side:
- SQLite → ``sqlite+aiosqlite:///<path>`` (aiosqlite is already a
  langgraph-checkpoint-sqlite transitive dep).
- PostgreSQL → ``postgresql+psycopg://...`` — psycopg v3 is a runtime
  dep and supports both sync and async over the same URL prefix
  (SQLAlchemy picks the mode based on ``create_async_engine`` vs
  ``create_engine``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

# URL building, engine creation, and the session factory live in the shared
# ``dap-database`` package (#778 Phase 1) — re-exported here so the rest of
# the auth stack (and tests) keep their existing import paths.
from dap_database import (
    async_url_for as _async_url_for,  # noqa: F401 — re-export for tests
)
from dap_database import (
    create_async_engine_for_url,
    make_async_session_factory,
)
from fastapi import Depends, Request
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dap_engine.persistence.models import OAuthAccountORM, UserORM

__all__ = [
    "AsyncSession",
    "create_async_engine_for_url",
    "get_async_session",
    "get_user_db",
    "make_async_session_factory",
]


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
#
# Reading the factory off ``app.state`` (rather than a module-level
# global) lets the engine lifespan own its lifecycle and lets tests swap
# the factory per-test via ``app.state.async_session_factory = ...``.


def _get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "async_session_factory", None
    )
    if factory is None:
        raise RuntimeError(
            "auth.db.async_session_factory missing from app.state — "
            "engine lifespan did not initialise auth (check DAP_AUTH_JWT_SECRET)"
        )
    return factory


async def get_async_session(
    factory: async_sessionmaker[AsyncSession] = Depends(_get_session_factory),
) -> AsyncGenerator[AsyncSession]:
    async with factory() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase[UserORM, uuid.UUID]]:
    # Pass OAuthAccountORM as the third argument so fastapi-users'
    # OAuth router can look up / create linked identities. Without this
    # the router silently 404s on every callback (no linked-account
    # storage available).
    yield SQLAlchemyUserDatabase(session, UserORM, OAuthAccountORM)
