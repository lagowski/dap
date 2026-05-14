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


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    dialect: str = getattr(request.app.state, "db_dialect", "sqlite")
    engine: Engine | None = getattr(request.app.state, "db_engine", None)
    return {
        "status": "ok",
        "service": "dap-engine",
        "version": "0.0.1",
        "db_dialect": dialect,
        "db_reachable": _is_db_reachable(engine),
        "timestamp": datetime.now(UTC).isoformat(),
    }
