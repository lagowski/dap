from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_db_reachable(engine: Engine | None) -> bool:
    """Quick SELECT 1 probe — used by the login page's DB status pill.

    Returns False on any exception so the response stays predictable
    even when the engine restarted under load or the network blipped.
    Logs at DEBUG (not WARNING) because /health is polled frequently
    and a transient blip shouldn't spam the operator logs.
    """
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        return True
    except Exception as e:
        logger.debug("/health DB probe failed: %s", e)
        return False


def _pool_stats(request: Request) -> dict[str, Any] | None:
    """Read pool stats from the checkpointer pool (non-blocking).

    Returns None when there is no pool (SQLite backend) so the caller
    can fall back to the SELECT 1 probe.
    """
    pool = getattr(request.app.state, "checkpointer_pool", None)
    if pool is None:
        return None
    try:
        return pool.get_stats()  # type: ignore[no-any-return]
    except Exception as e:
        logger.debug("/health pool stats failed: %s", e)
        return None


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Public health probe.

    Synchronous on purpose: SQLAlchemy's ``engine.connect()`` is a
    blocking call, so declaring this as ``def`` (not ``async def``)
    lets FastAPI run it in its threadpool. A slow DB probe under load
    can't tie up the event loop. (#391 review)

    When a PostgreSQL pool is available, pool stats are surfaced and
    ``db_reachable`` is derived from pool health rather than a fresh
    SELECT 1 probe that would mask stale-pool issues (#580).
    """
    dialect: str = getattr(request.app.state, "db_dialect", "sqlite")
    engine: Engine | None = getattr(request.app.state, "db_engine", None)

    stats = _pool_stats(request)
    if stats is not None:
        pool_size = stats.get("pool_size", 0)
        pool_available = stats.get("pool_available", 0)
        # Pool is reachable if it has any connections that aren't all stale.
        # A pool with size > 0 and available > 0 means healthy connections exist.
        # When pool_size == 0, the pool hasn't opened yet — fall back to probe.
        db_reachable = pool_available > 0 if pool_size > 0 else _is_db_reachable(engine)
        return {
            "status": "ok",
            "service": "dap-engine",
            "version": request.app.version,
            "db_dialect": dialect,
            "db_reachable": db_reachable,
            "pool_size": pool_size,
            "pool_available": pool_available,
            "pool_exhausted": stats.get("requests_waiting", 0) > 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return {
        "status": "ok",
        "service": "dap-engine",
        "version": request.app.version,
        "db_dialect": dialect,
        "db_reachable": _is_db_reachable(engine),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/version")
def version(request: Request) -> dict[str, str]:
    """Public engine version endpoint for bundle compatibility checks."""
    return {
        "service": "dap-engine",
        "version": request.app.version,
    }
