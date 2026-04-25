from __future__ import annotations

from typing import Any

from dap_runtimes import RuntimeRegistry
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/runtimes")


def _registry(request: Request) -> RuntimeRegistry:
    """Type-narrow Starlette state.runtime_registry (which is Any)."""
    registry: RuntimeRegistry = request.app.state.runtime_registry
    return registry


@router.get("")
async def list_runtimes(request: Request) -> list[dict[str, Any]]:
    registry = _registry(request)
    return [{"id": a.id, "displayName": a.display_name, "kind": a.kind} for a in registry.list()]


@router.get("/{runtime_id}/health")
async def runtime_health(runtime_id: str, request: Request) -> dict[str, Any]:
    registry = _registry(request)
    if not registry.has(runtime_id):
        raise HTTPException(status_code=404, detail=f"Runtime not found: {runtime_id}")
    adapter = registry.get(runtime_id)
    health = await adapter.healthcheck()
    return health.model_dump(exclude_none=True)
