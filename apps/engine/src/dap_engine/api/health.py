from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "dap-engine",
        "version": "0.0.1",
        "timestamp": datetime.now(UTC).isoformat(),
    }
