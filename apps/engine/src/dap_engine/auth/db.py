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
from pathlib import Path

from fastapi import Depends, Request
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dap_engine.persistence.db import _normalize_pg_prefix, detect_dialect
from dap_engine.persistence.models import UserORM

__all__ = [
    "AsyncSession",
    "create_async_engine_for_url",
    "get_async_session",
    "get_user_db",
    "make_async_session_factory",
]


def _async_url_for(database_url: str | None, db_path: str | None) -> str:
    """Build a SQLAlchemy async URL from the same inputs the sync engine uses.

    Mirrors ``create_app()``'s sync-engine selection logic exactly: only
    PostgreSQL URLs in ``database_url`` are honoured; SQLite URLs are
    ignored in favour of ``db_path`` so both engines always point at the
    same underlying SQLite file. Otherwise an operator setting
    ``DAP_DATABASE_URL=sqlite:///foo.db`` would silently get a different
    DB for auth than for the rest of the engine.

    Raises ``ValueError`` if neither input yields a usable URL —
    callers must validate config before reaching this helper.
    """
    if database_url and detect_dialect(database_url) == "postgresql":
        url = _normalize_pg_prefix(database_url)
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url
    # Sync engine treats anything that isn't a Postgres URL as "use db_path",
    # so do the same here. ``database_url`` carrying a SQLite URL is
    # effectively dead code on the sync side; honouring it asymmetrically
    # would diverge the auth and main databases.
    if db_path:
        absolute = Path(db_path).resolve()
        return f"sqlite+aiosqlite:///{absolute}"
    raise ValueError("Either database_url (postgresql) or db_path must be provided")


def create_async_engine_for_url(
    database_url: str | None,
    db_path: str | None,
) -> AsyncEngine:
    """Create the async engine used by fastapi-users.

    Pool sizes are smaller than the sync engine's — auth traffic is
    sparse compared to the runs/state endpoints. ``pool_pre_ping`` is on
    so a token-validation request after a long idle period doesn't fail
    on a NAT-dropped TCP connection.
    """
    url = _async_url_for(database_url, db_path)
    return create_async_engine(
        url,
        echo=False,
        future=True,
        pool_size=2,
        max_overflow=4,
        pool_pre_ping=True,
    )


def make_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


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
    yield SQLAlchemyUserDatabase(session, UserORM)
