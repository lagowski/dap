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
from fastapi import APIRouter, Depends, HTTPException, Request, status

from dap_engine.api.deps import get_registry
from dap_engine.auth.users import current_active_user
from dap_engine.execution.runner import DEFAULT_RECURSION_LIMIT
from dap_engine.persistence.db import detect_dialect, redact_database_url
from dap_engine.persistence.models import UserORM

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


# ---------------------------------------------------------------------------
# Admin-only instance settings view (#301, sub-C5, closes #331)
# ---------------------------------------------------------------------------


@router.get("/settings/admin")
async def get_admin_settings(
    request: Request,
    user: UserORM = Depends(current_active_user),
) -> dict[str, Any]:
    """Read-only snapshot of instance-wide config for the admin panel.

    Distinct from ``GET /settings`` (runtime / provider health) — this
    surface is for security-sensitive operator knobs the admin needs
    to verify at a glance: JWT lifetime, OAuth wiring, CORS allow-list,
    reset-token logging flag, storage backend. No mutations; instance
    config is env-var driven and requires a restart to change.

    Anti-enumeration: non-admins get ``404`` (mirrors the rest of the
    admin surface — sub-A4b2 series). Anonymous gets ``401`` from
    ``current_active_user``.

    **Never exposes secrets.** OAuth client_secrets and the JWT
    secret are presence-only — the response says ``True`` /
    ``False`` for whether they're configured, never the value.
    Mirrors the rule the ``/audit/events`` route applies to its
    own ``event_data`` payloads.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    config = request.app.state.config

    db_backend, db_location = _describe_database(config)
    cors = _describe_cors(config)

    return {
        "auth": {
            "jwt_secret_configured": bool(config.auth_jwt_secret),
            "access_ttl_seconds": config.auth_access_ttl_seconds,
            # Security-relevant: reset-token logging is dev-only.
            # The dashboard surfaces a warning when this is on.
            "log_reset_tokens": config.auth_log_reset_tokens,
        },
        "oauth": {
            "github": {
                "configured": bool(
                    config.oauth_github_client_id and config.oauth_github_client_secret
                ),
                "client_id_configured": bool(config.oauth_github_client_id),
                "client_secret_configured": bool(config.oauth_github_client_secret),
            },
            "google": {
                "configured": bool(
                    config.oauth_google_client_id and config.oauth_google_client_secret
                ),
                "client_id_configured": bool(config.oauth_google_client_id),
                "client_secret_configured": bool(config.oauth_google_client_secret),
            },
            "redirect_url": config.auth_oauth_redirect_url,
        },
        "cors": cors,
        "storage": {
            "backend": db_backend,
            "location": db_location,
        },
    }


def _describe_cors(config: Any) -> dict[str, Any]:
    """Return the *effective* CORS configuration.

    The engine treats ``cors_origins=None`` as "fall back to
    ``DEFAULT_CORS_ORIGINS`` (the local-dev allow-list)", **not**
    "permissive / all origins". Earlier sub-C5 versions exposed the
    raw config value, which let the dashboard mis-report unset config
    as "all origins" (Copilot review on PR #332).

    Now we resolve to what ``CORSMiddleware`` will actually use, plus
    a ``using_default`` flag the dashboard renders as a chip so an
    operator knows the value came from defaults rather than an
    explicit env var.
    """
    # Import locally to avoid a circular import with ``app.py``.
    from dap_engine.app import DEFAULT_CORS_ORIGINS  # noqa: PLC0415

    if config.cors_origins is None:
        return {
            "origins": list(DEFAULT_CORS_ORIGINS),
            "using_default": True,
        }
    return {
        "origins": list(config.cors_origins),
        "using_default": False,
    }


def _describe_database(config: Any) -> tuple[str, str]:
    """Return ``(backend, location)`` with credentials redacted.

    Uses ``persistence.db.detect_dialect`` for backend detection —
    the same helper ``create_app`` uses — so the answer matches
    what's actually running. Naively treating any non-None
    ``database_url`` as PostgreSQL (Copilot review on PR #332)
    would mis-label SQLite URLs and break the dashboard display.
    """
    url = getattr(config, "database_url", None)
    if url is None:
        # No DATABASE_URL set → engine uses the SQLite file at db_path.
        return "sqlite", str(Path(config.db_path).resolve())
    backend = detect_dialect(url)
    if backend == "sqlite":
        # SQLite-via-URL is rare but supported; show the path the
        # way an operator would type it.
        return "sqlite", url
    return "postgresql", redact_database_url(url)
