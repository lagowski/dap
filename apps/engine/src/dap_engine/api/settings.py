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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dap_runtimes import RuntimeRegistry
from dap_runtimes.adapters._providers import PROVIDER_REGISTRY
from dap_types import HealthStatus, RuntimeAdapter
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.api.deps import get_engine_config, get_registry, get_session
from dap_engine.auth.audit import record_audit_event
from dap_engine.auth.encryption import EncryptionError, encrypt_value
from dap_engine.auth.users import current_active_user
from dap_engine.execution.runner import DEFAULT_RECURSION_LIMIT
from dap_engine.persistence.db import detect_dialect, redact_database_url
from dap_engine.persistence.models import InstanceEnvVarORM, UserORM

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


# ---------------------------------------------------------------------------
# Admin-only instance env vars (#388)
# ---------------------------------------------------------------------------


# Posix env-var name shape: leading letter or underscore, then
# alphanumerics / underscores. Uppercase-only by convention — operators
# who name them otherwise are likely typoing.
_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# Refuse keys that could shadow engine bootstrap settings or hijack
# the subprocess loader. The merge layer would otherwise let an
# admin override DATABASE_URL or LD_PRELOAD via the instance store —
# powerful enough to brick the engine or sneak code into every shell
# we spawn. ``DAP_`` is reserved for the engine's own config.
_RESERVED_PREFIXES = ("DAP_", "POSTGRES_", "PYTHON")
_RESERVED_EXACT = frozenset({"DATABASE_URL", "PATH", "LD_LIBRARY_PATH", "LD_PRELOAD"})


def _validate_key(key: str) -> None:
    """Raise 422 if ``key`` is not a legal env-var name or is reserved."""
    if not key or not _KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid env-var name {key!r}: must match ^[A-Z_][A-Z0-9_]*$ "
                "(uppercase letters, digits and underscores; no leading digit)."
            ),
        )
    if key in _RESERVED_EXACT or any(key.startswith(p) for p in _RESERVED_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Env-var name {key!r} is reserved — these keys are owned by "
                "the engine or the subprocess loader and cannot be set via "
                "instance settings. Set them as OS env vars on the engine "
                "process if you need them."
            ),
        )


def _build_preview(plaintext: str) -> str:
    """Short non-secret hint shown in the admin UI listing.

    Goal: enough to tell ``ghp_`` apart from ``gho_`` at a glance,
    without leaking enough characters to weaken the secret. Eight
    chars on screen (``XXXX••••``) feels close to GitHub's own
    obfuscation in their UI.
    """
    head = plaintext[:4]
    return f"{head}••••"


def _require_admin(user: UserORM) -> None:
    """Anti-enumeration admin gate — non-admins get 404 (same shape as
    the read-only ``/settings/admin`` route at the top of this module)."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )


def _require_encryption_key(config: Any) -> str:
    """Refuse POST writes when no Fernet key is configured.

    Only the POST upsert path calls this helper — DELETE deliberately
    does not require the key (removing a row whose ciphertext we
    couldn't decrypt is a legitimate cleanup path after a botched
    rotation), and GET reads use the unencrypted ``preview`` column
    so the listing works without the key either.

    Writing rows we couldn't decrypt on restart would be worse than
    rejecting the write — the admin can rotate / set
    ``DAP_INSTANCE_ENV_VARS_KEY`` and try again. ``503`` (rather than
    500) signals "operator config issue", which is what this is.
    """
    key = config.instance_env_vars_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Instance env-var store is not configured: set "
                "DAP_INSTANCE_ENV_VARS_KEY to a Fernet key (generate one "
                "with `python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'`)."
            ),
        )
    return str(key)


class InstanceEnvVarPreview(BaseModel):
    """Admin GET shape — never carries the plaintext."""

    key: str
    preview: str
    value_set: bool


class InstanceEnvVarListing(BaseModel):
    env_vars: list[InstanceEnvVarPreview]


@router.get(
    "/settings/admin/env-vars",
    response_model=InstanceEnvVarListing,
)
def list_instance_env_vars(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> InstanceEnvVarListing:
    """List instance env vars with masked previews — never the raw value.

    Mirrors the read shape the dashboard uses for other secret stores:
    one row per key with ``value_set=True`` and a short non-secret
    ``preview`` (first four chars + four bullets). The full value
    only ever leaves the engine into the subprocess env at run time.
    """
    _require_admin(user)
    rows = (
        session.execute(select(InstanceEnvVarORM).order_by(InstanceEnvVarORM.key)).scalars().all()
    )
    return InstanceEnvVarListing(
        env_vars=[
            InstanceEnvVarPreview(key=row.key, preview=row.preview, value_set=True) for row in rows
        ]
    )


@router.post(
    "/settings/admin/env-vars",
    response_model=InstanceEnvVarListing,
)
def upsert_instance_env_vars(
    body: dict[str, str],
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    config: Any = Depends(get_engine_config),
) -> InstanceEnvVarListing:
    """Upsert one or more instance env vars.

    Request body is a flat ``{key: value}`` dict — matches the shape
    the issue (#388) calls out. Each key is validated; each value is
    encrypted with the configured Fernet key before it ever touches
    the DB. An audit event is recorded per key (``created`` or
    ``updated``); the value never appears in ``event_data`` — only
    the key name.

    Typed as ``dict[str, str]`` so FastAPI's body validation rejects
    non-string values with a 422 before the handler runs — keeps the
    OpenAPI schema honest about what we accept.
    """
    _require_admin(user)
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must be a non-empty {key: value} mapping.",
        )
    encryption_key = _require_encryption_key(config)

    # Validate everything first so a partial batch never lands — either
    # the whole POST applies or nothing does (the session rollback
    # in get_session handles the latter on any raise below).
    # ``isinstance(value, str)`` is guaranteed by the typed body above;
    # only emptiness still needs an application-layer check (Pydantic
    # accepts ``""`` as a valid string).
    for key, value in body.items():
        _validate_key(key)
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Value for {key!r} is empty — use DELETE to remove an "
                    "env var, not POST with an empty string."
                ),
            )

    # Single SELECT keyed by ``key IN (body.keys())`` — avoids the N+1
    # the per-key lookup version did on bulk upserts. Two admins POSTing
    # the same key concurrently can still race past this check and both
    # try to INSERT; the UNIQUE constraint on ``key`` makes one of them
    # roll back at commit. That's an acceptable corner case for a
    # single-operator install (the loser sees 5xx and retries).
    existing_rows = (
        session.execute(select(InstanceEnvVarORM).where(InstanceEnvVarORM.key.in_(body.keys())))
        .scalars()
        .all()
    )
    existing_by_key = {row.key: row for row in existing_rows}

    now = datetime.now(UTC)
    for key, value in body.items():
        try:
            ciphertext = encrypt_value(value, key=encryption_key)
        except EncryptionError as exc:
            # Bad Fernet key → 503 (same class as missing key). The
            # operator needs to fix DAP_INSTANCE_ENV_VARS_KEY.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        preview = _build_preview(value)
        existing = existing_by_key.get(key)
        if existing is None:
            session.add(
                InstanceEnvVarORM(
                    key=key,
                    ciphertext=ciphertext,
                    preview=preview,
                    created_at=now,
                    updated_at=now,
                )
            )
            event_type = "settings.env_var.created"
        else:
            existing.ciphertext = ciphertext
            existing.preview = preview
            existing.updated_at = now
            event_type = "settings.env_var.updated"

        # event_data carries the *key name only* — never the value.
        # The audit log JSONB is admin-visible at /audit/events; any
        # leak here would defeat the whole point of encrypting at rest.
        record_audit_event(
            session,
            user_id=user.id,
            event_type=event_type,
            event_data={"key": key},
        )

    # Flush so the listing below reflects the inserts/updates within
    # the same transaction.
    session.flush()
    rows = (
        session.execute(select(InstanceEnvVarORM).order_by(InstanceEnvVarORM.key)).scalars().all()
    )
    return InstanceEnvVarListing(
        env_vars=[
            InstanceEnvVarPreview(key=row.key, preview=row.preview, value_set=True) for row in rows
        ]
    )


@router.delete(
    "/settings/admin/env-vars/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_instance_env_var(
    key: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Response:
    """Remove one instance env var. Unknown key returns 404 (no
    enumeration concern — admin already knows what they're deleting)."""
    _require_admin(user)
    row = session.execute(
        select(InstanceEnvVarORM).where(InstanceEnvVarORM.key == key)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance env var {key!r} not found.",
        )
    session.delete(row)
    record_audit_event(
        session,
        user_id=user.id,
        event_type="settings.env_var.deleted",
        event_data={"key": key},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
