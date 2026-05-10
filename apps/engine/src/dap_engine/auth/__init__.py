"""Authentication & user management (v0.3, see #299).

Public surface is intentionally minimal: callers should import
``current_active_user`` (FastAPI dependency) and ``fastapi_users`` (the
configured FastAPIUsers instance for mounting routers). Everything else
is implementation detail.

Architecture note: this module ships an async SQLAlchemy engine that
runs alongside the existing sync engine in ``persistence/db.py``. Both
share the same database file/URL but maintain independent pools. This
isolates the fastapi-users requirement (which is async-only) from the
rest of the engine's sync codebase, with the trade-off of running two
connection pools per process.
"""

from __future__ import annotations

from dap_engine.auth.users import current_active_user, fastapi_users

__all__ = [
    "current_active_user",
    "fastapi_users",
]
