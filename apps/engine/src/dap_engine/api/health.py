from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    dialect: str = getattr(request.app.state, "db_dialect", "sqlite")
    return {
        "status": "ok",
        "service": "dap-engine",
        "version": "0.0.1",
        "db_dialect": dialect,
        "timestamp": datetime.now(UTC).isoformat(),
    }
