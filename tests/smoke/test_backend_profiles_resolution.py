"""Unit coverage for pipeline-export/2 backend profile resolution."""

from __future__ import annotations

import pytest
from dap_engine.execution.backend_profiles import (
    merge_backend_profile_env_layers,
    resolve_backend_profile,
)


def test_backend_profile_override_precedence_prefers_node_then_agent() -> None:
    resolved = resolve_backend_profile(
        backend_profiles={
            "available": {
                "by-node": {"runtime_config": {"source": "node"}},
                "by-agent": {"runtime_config": {"source": "agent"}},
                "by-name": {"runtime_config": {"source": "name"}},
                "by-role": {"runtime_config": {"source": "role"}},
            },
            "agent_assignments": {
                "default_profile": "by-role",
                "overrides": {
                    "role-a": "by-role",
                    "Agent A": "by-name",
                    "agent-a": "by-agent",
                    "node-a": "by-node",
                },
            },
        },
        node_id="node-a",
        agent_id="agent-a",
        agent_name="Agent A",
        agent_role="role-a",
        base_runtime_id="api-call",
        base_runtime_config={},
        env_layers={},
    )

    assert resolved.profile_id == "by-node"
    assert resolved.runtime_config == {"source": "node"}


def test_backend_profile_expands_flat_config_and_collection_env_refs() -> None:
    resolved = resolve_backend_profile(
        backend_profiles={
            "available": {
                "profile-a": {
                    "api_key": {"env": "API_KEY"},
                    "headers": ["Bearer ${API_KEY}", "$MODEL"],
                    "nested": {"token": {"env_var": "API_KEY"}},
                    "requires_env": ["API_KEY"],
                }
            },
            "agent_assignments": {"default_profile": "profile-a", "overrides": {}},
        },
        node_id="node-a",
        agent_id="agent-a",
        agent_name="Agent A",
        agent_role="role-a",
        base_runtime_id="api-call",
        base_runtime_config={"model": "base"},
        env_layers={"API_KEY": "secret", "MODEL": "haiku"},
    )

    assert resolved.runtime_config == {
        "model": "base",
        "api_key": "secret",
        "headers": ["Bearer ${API_KEY}", "haiku"],
        "nested": {"token": "secret"},
        "env": {"API_KEY": "secret"},
    }


def test_backend_profile_env_layers_do_not_read_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_ONLY_SECRET", "must-not-leak")

    env_layers = merge_backend_profile_env_layers(
        instance_env_vars=None,
        project_env_vars=None,
    )
    resolved = resolve_backend_profile(
        backend_profiles={
            "available": {
                "profile-a": {
                    "runtime_config": {"api_key": "$HOST_ONLY_SECRET"},
                    "requires_env": ["HOST_ONLY_SECRET"],
                }
            },
            "agent_assignments": {"default_profile": "profile-a", "overrides": {}},
        },
        node_id="node-a",
        agent_id="agent-a",
        agent_name="Agent A",
        agent_role="role-a",
        base_runtime_id="api-call",
        base_runtime_config={},
        env_layers=env_layers,
    )

    assert "HOST_ONLY_SECRET" not in env_layers
    assert resolved.runtime_config == {"api_key": "$HOST_ONLY_SECRET"}
