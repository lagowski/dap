from __future__ import annotations

from typing import Any

from dap_runtimes import RuntimeRegistry
from fastapi import APIRouter, Depends, HTTPException

from dap_engine.api.deps import get_registry

router = APIRouter(prefix="/runtimes", tags=["runtimes"])


@router.get("")
async def list_runtimes(
    registry: RuntimeRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    return [{"id": a.id, "displayName": a.display_name, "kind": a.kind} for a in registry.list()]


@router.get("/{runtime_id}/health")
async def runtime_health(
    runtime_id: str,
    registry: RuntimeRegistry = Depends(get_registry),
) -> dict[str, Any]:
    if not registry.has(runtime_id):
        raise HTTPException(status_code=404, detail=f"Runtime not found: {runtime_id}")
    adapter = registry.get(runtime_id)
    health = await adapter.healthcheck()
    return health.model_dump(exclude_none=True)
