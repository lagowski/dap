"""Runtime resolution for pipeline-export/2 backend profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Profiles to re-run the node on, in order, when it fails with a backend
    # error (#935). Each is resolved exactly like a primary; never nested.
    fallbacks: tuple[ResolvedBackendProfile, ...] = field(default=())


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
      agent name, or role. An override is a profile id, or an object
      ``{"profile": id, "fallback": [id, ...]}`` (#935); an object without
      ``profile`` keeps the default profile and adds the fallback chain.

    Merge order preserves existing behaviour unless a profile is selected:
    agent version config < selected profile config. Node-level overrides are
    still applied later by ``NodeContext``.
    """

    profile_id, fallback_ids = _selected_assignment(
        backend_profiles=backend_profiles,
        node_id=node_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_role=agent_role,
    )
    if env_layers is None:
        env_layers = merge_backend_profile_env_layers(
            instance_env_vars=instance_env_vars,
            project_env_vars=project_env_vars,
        )
    primary = _resolve_profile(
        backend_profiles,
        profile_id,
        base_runtime_id=base_runtime_id,
        base_runtime_config=base_runtime_config,
        env_layers=env_layers,
    )
    # An unknown fallback id is skipped: saves and imports reject it, so it
    # can only reach a run through a row written before #935.
    fallbacks = tuple(
        _resolve_profile(
            backend_profiles,
            fallback_id,
            base_runtime_id=base_runtime_id,
            base_runtime_config=base_runtime_config,
            env_layers=env_layers,
        )
        for fallback_id in fallback_ids
        if _profile_by_id(backend_profiles, fallback_id) is not None
    )
    if not fallbacks:
        return primary
    return ResolvedBackendProfile(
        runtime_id=primary.runtime_id,
        runtime_config=primary.runtime_config,
        profile_id=primary.profile_id,
        fallbacks=fallbacks,
    )


def _resolve_profile(
    backend_profiles: dict[str, Any] | None,
    profile_id: str | None,
    *,
    base_runtime_id: str,
    base_runtime_config: dict[str, Any],
    env_layers: dict[str, str],
) -> ResolvedBackendProfile:
    profile = _profile_by_id(backend_profiles, profile_id) if profile_id is not None else None
    if profile is None:
        return ResolvedBackendProfile(
            runtime_id=base_runtime_id,
            runtime_config=dict(base_runtime_config),
            profile_id=None,
        )

    runtime_id = profile.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        runtime_id = base_runtime_id

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


def _selected_assignment(
    *,
    backend_profiles: dict[str, Any] | None,
    node_id: str,
    agent_id: str,
    agent_name: str,
    agent_role: str,
) -> tuple[str | None, list[str]]:
    """The profile id and fallback ids assigned to a node."""
    if not isinstance(backend_profiles, dict):
        return None, []

    assignments = backend_profiles.get("agent_assignments")
    if not isinstance(assignments, dict):
        return None, []

    default_profile = assignments.get("default_profile")
    if not isinstance(default_profile, str) or not default_profile:
        default_profile = None

    overrides = assignments.get("overrides")
    if isinstance(overrides, dict):
        for key in (node_id, agent_id, agent_name, agent_role):
            override = overrides.get(key)
            if isinstance(override, str) and override:
                return override, []
            if isinstance(override, dict):
                profile_id = override.get("profile")
                if not isinstance(profile_id, str) or not profile_id:
                    profile_id = default_profile
                return profile_id, _string_list(override.get("fallback"))

    return default_profile, []


def override_profile_id(override: Any) -> str | None:
    """The profile id of one ``overrides`` entry, string or object form."""
    if isinstance(override, str):
        return override
    if isinstance(override, dict):
        profile_id = override.get("profile")
        return profile_id if isinstance(profile_id, str) else None
    return None


def fallback_chain_errors(backend_profiles: dict[str, Any] | None) -> list[str]:
    """Validation errors for the ``fallback`` lists in ``agent_assignments.overrides``.

    A fallback must be a list of ids declared in ``available`` (#935). Used by
    pipeline save/import, the run trigger and the backend inspection endpoint.
    """
    if not isinstance(backend_profiles, dict):
        return []
    assignments = backend_profiles.get("agent_assignments")
    if not isinstance(assignments, dict):
        return []
    overrides = assignments.get("overrides")
    if not isinstance(overrides, dict):
        return []

    available = backend_profiles.get("available")
    known = set(available) if isinstance(available, dict) else set()
    errors: list[str] = []
    for key, override in overrides.items():
        if not isinstance(override, dict) or "fallback" not in override:
            continue
        where = f"backend_profiles.agent_assignments.overrides.{key}.fallback"
        fallback = override["fallback"]
        if not isinstance(fallback, list) or not all(isinstance(i, str) for i in fallback):
            errors.append(f"{where}: must be a list of profile ids")
            continue
        errors.extend(
            f"{where}: unknown profile id {profile_id!r}"
            for profile_id in fallback
            if profile_id not in known
        )
    return errors


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
