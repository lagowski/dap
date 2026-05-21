"""Runtime resolution for pipeline-export/2 backend profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_PROFILE_METADATA_KEYS = {
    "label",
    "description",
    "requires_env",
    "requires_service",
    "runtime_id",
    "runtime_config",
    "config",
}
_BRACED_ENV_REF_MIN_LENGTH = 4


@dataclass(frozen=True)
class ResolvedBackendProfile:
    runtime_id: str
    runtime_config: dict[str, Any]
    profile_id: str | None = None


def resolve_backend_profile(
    *,
    backend_profiles: dict[str, Any] | None,
    node_id: str,
    agent_id: str,
    agent_name: str,
    agent_role: str,
    base_runtime_id: str,
    base_runtime_config: dict[str, Any],
    instance_env_vars: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
    env_layers: dict[str, str] | None = None,
) -> ResolvedBackendProfile:
    """Resolve the backend profile for a node/agent execution.

    ``backend_profiles`` is intentionally loose JSON for bundle compatibility.
    The resolver therefore accepts a small, forward-compatible shape:

    - ``available[profile_id].runtime_id`` optionally changes the adapter.
    - ``available[profile_id].runtime_config`` or ``config`` contributes
      runtime config keys.
    - flat non-metadata keys on the profile also contribute runtime config.
    - ``agent_assignments.default_profile`` applies to every node.
    - ``agent_assignments.overrides`` can target node id, local agent id,
      agent name, or role.

    Merge order preserves existing behaviour unless a profile is selected:
    agent version config < selected profile config. Node-level overrides are
    still applied later by ``NodeContext``.
    """

    profile_id = _selected_profile_id(
        backend_profiles=backend_profiles,
        node_id=node_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_role=agent_role,
    )
    if profile_id is None:
        return ResolvedBackendProfile(
            runtime_id=base_runtime_id,
            runtime_config=dict(base_runtime_config),
            profile_id=None,
        )

    profile = _profile_by_id(backend_profiles, profile_id)
    if profile is None:
        return ResolvedBackendProfile(
            runtime_id=base_runtime_id,
            runtime_config=dict(base_runtime_config),
            profile_id=None,
        )

    runtime_id = profile.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        runtime_id = base_runtime_id

    if env_layers is None:
        env_layers = merge_backend_profile_env_layers(
            instance_env_vars=instance_env_vars,
            project_env_vars=project_env_vars,
        )
    profile_config = _profile_runtime_config(profile, env_layers)
    profile_config = _with_required_env(
        profile_config,
        requires_env=_string_list(profile.get("requires_env")),
        env_layers=env_layers,
    )

    return ResolvedBackendProfile(
        runtime_id=runtime_id,
        runtime_config={**base_runtime_config, **profile_config},
        profile_id=profile_id,
    )


def _selected_profile_id(
    *,
    backend_profiles: dict[str, Any] | None,
    node_id: str,
    agent_id: str,
    agent_name: str,
    agent_role: str,
) -> str | None:
    if not isinstance(backend_profiles, dict):
        return None

    assignments = backend_profiles.get("agent_assignments")
    if not isinstance(assignments, dict):
        return None

    overrides = assignments.get("overrides")
    if isinstance(overrides, dict):
        for key in (node_id, agent_id, agent_name, agent_role):
            profile_id = overrides.get(key)
            if isinstance(profile_id, str) and profile_id:
                return profile_id

    default_profile = assignments.get("default_profile")
    return default_profile if isinstance(default_profile, str) and default_profile else None


def _profile_by_id(
    backend_profiles: dict[str, Any] | None,
    profile_id: str,
) -> dict[str, Any] | None:
    if not isinstance(backend_profiles, dict):
        return None

    available = backend_profiles.get("available")
    if not isinstance(available, dict):
        return None

    profile = available.get(profile_id)
    return profile if isinstance(profile, dict) else None


def _profile_runtime_config(
    profile: dict[str, Any],
    env_layers: dict[str, str],
) -> dict[str, Any]:
    raw_config = profile.get("runtime_config")
    if not isinstance(raw_config, dict):
        raw_config = profile.get("config")
    if isinstance(raw_config, dict):
        return {
            key: _expand_env_references(value, env_layers)
            for key, value in raw_config.items()
            if isinstance(key, str)
        }

    return {
        key: _expand_env_references(value, env_layers)
        for key, value in profile.items()
        if isinstance(key, str) and key not in _PROFILE_METADATA_KEYS
    }


def _with_required_env(
    runtime_config: dict[str, Any],
    *,
    requires_env: list[str],
    env_layers: dict[str, str],
) -> dict[str, Any]:
    if not requires_env:
        return runtime_config

    existing_env = runtime_config.get("env")
    merged_env: dict[str, str] = {}
    if isinstance(existing_env, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in existing_env.items()
    ):
        merged_env = {key: value for key, value in existing_env.items()}
    for key in requires_env:
        if key in env_layers and key not in merged_env:
            merged_env[key] = env_layers[key]

    if not merged_env:
        return runtime_config
    return {**runtime_config, "env": merged_env}


def _expand_env_references(value: Any, env_layers: dict[str, str]) -> Any:
    if isinstance(value, str):
        env_key = _env_reference_key(value)
        return env_layers.get(env_key, value) if env_key is not None else value

    if isinstance(value, list):
        return [_expand_env_references(item, env_layers) for item in value]

    if isinstance(value, dict):
        if set(value) == {"env"} and isinstance(value["env"], str):
            return env_layers.get(value["env"], value)
        if set(value) == {"env_var"} and isinstance(value["env_var"], str):
            return env_layers.get(value["env_var"], value)
        return {
            key: _expand_env_references(item, env_layers)
            for key, item in value.items()
            if isinstance(key, str)
        }

    return value


def _env_reference_key(value: str) -> str | None:
    if value.startswith("${") and value.endswith("}") and len(value) >= _BRACED_ENV_REF_MIN_LENGTH:
        return value[2:-1]
    if value.startswith("$") and len(value) > 1 and value[1:].replace("_", "").isalnum():
        return value[1:]
    return None


def merge_backend_profile_env_layers(
    *,
    instance_env_vars: dict[str, str] | None,
    project_env_vars: dict[str, str] | None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if instance_env_vars:
        merged.update(instance_env_vars)
    if project_env_vars:
        merged.update(project_env_vars)
    return merged


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
