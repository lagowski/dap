"""Database URL / dialect helpers.

Moved out of ``dap_engine.persistence.db`` and ``dap_engine.auth.db``
(#778 Phase 1) so the engine and the CLI share one implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

__all__ = [
    "async_url_for",
    "detect_dialect",
    "normalize_pg_prefix",
    "pg_conn_string",
    "pg_sync_url",
    "redact_database_url",
]


def detect_dialect(url: str) -> Literal["sqlite", "postgresql"]:
    """Return ``"postgresql"`` for postgres-family URLs, else ``"sqlite"``.

    Accepts ``postgresql+asyncpg://``, ``postgresql+psycopg://``, bare
    ``postgresql://``, and heroku-style ``postgres://`` — all routed to the
    PostgreSQL branch.
    """
    return "postgresql" if url.startswith(("postgresql", "postgres://")) else "sqlite"


def normalize_pg_prefix(url: str) -> str:
    """Rewrite heroku-style ``postgres://`` to ``postgresql://`` for SQLAlchemy.

    SQLAlchemy 1.4+ rejects the bare ``postgres://`` scheme; normalize at
    consumption time so callers can pass either form.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def pg_sync_url(database_url: str) -> str:
    """Return a SQLAlchemy URL guaranteed to use psycopg v3 as the sync driver.

    Normalizes ``postgres://`` to ``postgresql://``, rewrites ``+asyncpg`` to
    ``+psycopg``, and forces ``+psycopg`` on bare ``postgresql://`` URLs.
    The bare scheme is critical: SQLAlchemy defaults bare ``postgresql://``
    to psycopg2, which is not a project dependency, so create_engine would
    fail at runtime without this rewrite.
    """
    url = normalize_pg_prefix(database_url)
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def pg_conn_string(database_url: str) -> str:
    """Return a bare ``postgresql://`` conn string for psycopg (e.g. AsyncPostgresSaver)."""
    url = normalize_pg_prefix(database_url)
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def redact_database_url(url: str) -> str:
    """Mask the password in a database URL for log/UI display.

    Uses SQLAlchemy's ``make_url`` + ``render_as_string(hide_password=True)``
    so URL-encoded passwords, IPv6 hosts, query params, and other quirks of
    real-world database URLs round-trip safely. A naive string-splitting
    redaction would leak credentials on URLs whose password contains ``@``
    or other edge characters (Copilot review on PR #332 / #419).

    Falls back to the input string if SQLAlchemy can't parse the URL — we
    prefer surfacing a raw value over silently stripping something
    important. Callers that log this should also fail loudly enough that
    a malformed URL becomes obvious quickly.
    """
    try:
        return make_url(url).render_as_string(hide_password=True)
    except ArgumentError:
        return url


def async_url_for(database_url: str | None, db_path: str | None) -> str:
    """Build a SQLAlchemy async URL from the same inputs the sync engine uses.

    Mirrors the sync-engine selection logic exactly: only PostgreSQL URLs in
    ``database_url`` are honoured; SQLite URLs are ignored in favour of
    ``db_path`` so both engines always point at the same underlying SQLite
    file. Otherwise an operator setting ``DAP_DATABASE_URL=sqlite:///foo.db``
    would silently get a different DB for auth than for the rest of the
    engine.

    Raises ``ValueError`` if neither input yields a usable URL —
    callers must validate config before reaching this helper.
    """
    if database_url and detect_dialect(database_url) == "postgresql":
        # psycopg v3 serves both sync and async over the same ``+psycopg``
        # prefix; SQLAlchemy picks the mode from create_async_engine.
        return pg_sync_url(database_url)
    if db_path:
        absolute = Path(db_path).resolve()
        return f"sqlite+aiosqlite:///{absolute}"
    raise ValueError("Either database_url (postgresql) or db_path must be provided")
