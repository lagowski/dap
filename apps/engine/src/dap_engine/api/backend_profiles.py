"""Backend profile inspection for pipeline bundle imports."""

from __future__ import annotations

import os
import shutil
from typing import Any

from sqlalchemy.orm import Session

from dap_engine.api.schemas import (
    BackendProfileInspection,
    BackendProfilesInspectionResponse,
    PipelineImportRequest,
)
from dap_engine.persistence import repository as repo

_SERVICE_COMMAND_ALIASES = {
    "claude-cli": "claude",
}


def inspect_backend_profiles(
    payload: PipelineImportRequest,
    session: Session,
) -> BackendProfilesInspectionResponse:
    """Evaluate bundle backend profiles against this engine instance.

    The import endpoint persists ``backend_profiles`` unchanged for backward
    compatibility. This helper gives the dashboard a typed, non-secret preview
    before import: which profile ids exist, what they require, and whether the
    engine can satisfy those requirements from process env, instance env vars,
    and local CLI availability.
    """

    backend_profiles = payload.backend_profiles
    if not isinstance(backend_profiles, dict):
        return BackendProfilesInspectionResponse()

    available = backend_profiles.get("available")
    if not isinstance(available, dict):
        return BackendProfilesInspectionResponse(
            default_profile=_default_profile(backend_profiles),
            overrides=_overrides(backend_profiles),
        )

    required_env = _required_env_keys(available)
    configured_env = _configured_env_keys(required_env, session)

    inspections = [
        _inspect_profile(profile_id, raw_profile, configured_env)
        for profile_id, raw_profile in sorted(available.items())
    ]
    return BackendProfilesInspectionResponse(
        default_profile=_default_profile(backend_profiles),
        overrides=_overrides(backend_profiles),
        profiles=inspections,
    )


def _inspect_profile(
    profile_id: str,
    raw_profile: Any,
    configured_env: set[str],
) -> BackendProfileInspection:
    if not isinstance(raw_profile, dict):
        return BackendProfileInspection(
            id=profile_id,
            label=profile_id,
            available=False,
        )

    requires_env = _string_list(raw_profile.get("requires_env"))
    missing_env = sorted(key for key in requires_env if key not in configured_env)

    requires_service = raw_profile.get("requires_service")
    if not isinstance(requires_service, str) or not requires_service.strip():
        requires_service = None

    service_available = (
        _service_available(requires_service) if requires_service is not None else None
    )
    profile_available = not missing_env and service_available is not False

    label = raw_profile.get("label")
    description = raw_profile.get("description")
    return BackendProfileInspection(
        id=profile_id,
        label=label if isinstance(label, str) and label.strip() else profile_id,
        description=description if isinstance(description, str) else None,
        requires_env=requires_env,
        missing_env=missing_env,
        requires_service=requires_service,
        service_available=service_available,
        available=profile_available,
    )


def _required_env_keys(available: dict[str, Any]) -> set[str]:
    required: set[str] = set()
    for raw_profile in available.values():
        if isinstance(raw_profile, dict):
            required.update(_string_list(raw_profile.get("requires_env")))
    return required


def _configured_env_keys(keys: set[str], session: Session) -> set[str]:
    if not keys:
        return set()

    process_keys = {key for key in keys if os.environ.get(key)}
    instance_keys = set(repo.find_env_vars_by_keys(session, keys).keys())
    return process_keys | instance_keys


def _service_available(service: str) -> bool:
    command = _SERVICE_COMMAND_ALIASES.get(service, service)
    return shutil.which(command) is not None


def _default_profile(backend_profiles: dict[str, Any]) -> str | None:
    assignments = backend_profiles.get("agent_assignments")
    if not isinstance(assignments, dict):
        return None
    default_profile = assignments.get("default_profile")
    return default_profile if isinstance(default_profile, str) else None


def _overrides(backend_profiles: dict[str, Any]) -> dict[str, str]:
    assignments = backend_profiles.get("agent_assignments")
    if not isinstance(assignments, dict):
        return {}

    overrides = assignments.get("overrides")
    if not isinstance(overrides, dict):
        return {}

    return {
        agent_id: profile_id
        for agent_id, profile_id in overrides.items()
        if isinstance(agent_id, str) and isinstance(profile_id, str)
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item.strip()})
