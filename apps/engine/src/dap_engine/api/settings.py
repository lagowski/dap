"""GET /settings — snapshot of operator-facing engine configuration.

Read-only by design (per #55): the dashboard reads this to populate the
``/settings`` page; actual configuration lives in env vars and the
launch flags. One endpoint avoids the dashboard fanning out to N
healthcheck calls in serial.

Returns three sections:

- ``runtimes`` — id / display_name / kind / availability / version /
  missing-env list for every registered runtime adapter.
- ``providers`` — per-api-call provider: id, display name, default
  env var name, whether that env var is currently set.
- ``engine`` — version, configured DB paths, recursion limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_runtimes.adapters._providers import PROVIDER_REGISTRY
from dap_types import HealthStatus, RuntimeAdapter
from fastapi import APIRouter, Depends, Request

from dap_engine.api.deps import get_registry
from dap_engine.execution.runner import DEFAULT_RECURSION_LIMIT

logger = logging.getLogger("dap.engine.api.settings")

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def get_settings(
    request: Request,
    registry: RuntimeRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Assemble the operator settings view (runtimes + providers + engine)."""
    runtimes = await _collect_runtimes(registry)
    providers = _collect_providers()
    engine = _collect_engine_info(request)
    return {
        "runtimes": runtimes,
        "providers": providers,
        "engine": engine,
    }


async def _collect_runtimes(registry: RuntimeRegistry) -> list[dict[str, Any]]:
    """One row per registered runtime adapter.

    Healthchecks fan out concurrently — some adapters shell out to a CLI
    for ``--version`` and have multi-second timeouts; serialising them
    would make ``/settings`` add up to those latencies. A single adapter
    that throws is reported as unavailable rather than failing the whole
    endpoint, so the operator still sees the rest of the table.
    """
    adapters = registry.list()
    healths = await asyncio.gather(
        *(_safe_healthcheck(adapter) for adapter in adapters),
        return_exceptions=False,
    )
    rows: list[dict[str, Any]] = []
    for adapter, health in zip(adapters, healths, strict=True):
        rows.append(
            {
                "id": adapter.id,
                "display_name": adapter.display_name,
                "kind": adapter.kind,
                "available": health.available,
                "version": health.version,
                "missing": health.missing,
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


async def _safe_healthcheck(adapter: RuntimeAdapter) -> HealthStatus:
    """Call ``adapter.healthcheck()`` but never propagate exceptions.

    A buggy adapter shouldn't take down the entire settings page.
    """
    try:
        return await adapter.healthcheck()
    except Exception as exc:
        logger.exception("healthcheck failed for runtime %s", adapter.id)
        return HealthStatus(
            available=False,
            missing=[f"healthcheck raised: {type(exc).__name__}: {exc}"],
        )


def _collect_providers() -> list[dict[str, Any]]:
    """One row per api-call provider; uses the registry metadata only.

    Reading metadata avoids importing the SDK for every provider on the
    settings request — same reason ``ApiCallAdapter.healthcheck`` reads
    the registry instead of dispatching to each provider module.
    """
    rows: list[dict[str, Any]] = []
    for provider_id, info in PROVIDER_REGISTRY.items():
        configured: bool
        if info.default_env_var is None:
            # openai-compat: per-agent env var, not knowable here.
            configured = False
        else:
            configured = bool(os.environ.get(info.default_env_var))
        rows.append(
            {
                "id": provider_id,
                "display_name": info.display_name,
                "default_env_var": info.default_env_var,
                "configured": configured,
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


def _collect_engine_info(request: Request) -> dict[str, Any]:
    """Engine-level details — paths, version, recursion cap.

    ``version`` is the value passed to ``FastAPI(version=...)`` in
    ``app.py`` so we share a single source of truth with /health.
    """
    config = request.app.state.config
    db_path = Path(config.db_path).resolve()
    checkpoint_db_path = db_path.with_suffix(".checkpoints.db")
    return {
        "version": request.app.version,
        "db_path": str(db_path),
        "checkpoint_db_path": str(checkpoint_db_path),
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }
