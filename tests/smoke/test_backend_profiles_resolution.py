"""Unit coverage for pipeline-export/2 backend profile resolution."""

from __future__ import annotations

import pytest
from dap_engine.execution.backend_profiles import (
    fallback_chain_errors,
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


# ---------------------------------------------------------------------------
# Fallback chain (#935)
# ---------------------------------------------------------------------------

_FALLBACK_PROFILES = {
    "available": {
        "claude-primary": {"runtime_id": "claude-code", "runtime_config": {"model": "opus"}},
        "claude-secondary": {
            "runtime_id": "claude-code",
            "runtime_config": {"model": "sonnet", "api_key": "${SECONDARY_KEY}"},
            "requires_env": ["SECONDARY_KEY"],
        },
        "codex-fallback": {"runtime_id": "codex", "model": "gpt"},
    },
    "agent_assignments": {
        "default_profile": "claude-primary",
        "overrides": {
            "coder": {
                "profile": "claude-primary",
                "fallback": ["claude-secondary", "codex-fallback"],
            }
        },
    },
}


def test_override_object_resolves_primary_and_each_fallback_like_a_primary() -> None:
    resolved = resolve_backend_profile(
        backend_profiles=_FALLBACK_PROFILES,
        node_id="coder",
        agent_id="agent-a",
        agent_name="Agent A",
        agent_role="role-a",
        base_runtime_id="api-call",
        base_runtime_config={"timeout": 5},
        env_layers={"SECONDARY_KEY": "secret"},
    )

    assert resolved.profile_id == "claude-primary"
    assert resolved.runtime_id == "claude-code"
    assert resolved.runtime_config == {"timeout": 5, "model": "opus"}
    assert [f.profile_id for f in resolved.fallbacks] == ["claude-secondary", "codex-fallback"]
    secondary, codex = resolved.fallbacks
    assert secondary.runtime_id == "claude-code"
    assert secondary.runtime_config == {
        "timeout": 5,
        "model": "sonnet",
        "api_key": "secret",
        "env": {"SECONDARY_KEY": "secret"},
    }
    assert codex.runtime_id == "codex"
    assert codex.runtime_config == {"timeout": 5, "model": "gpt"}
    assert secondary.fallbacks == ()


def test_string_assignment_has_no_fallbacks() -> None:
    resolved = resolve_backend_profile(
        backend_profiles=_FALLBACK_PROFILES,
        node_id="tester",
        agent_id="agent-b",
        agent_name="Agent B",
        agent_role="role-b",
        base_runtime_id="api-call",
        base_runtime_config={},
        env_layers={},
    )

    assert resolved.profile_id == "claude-primary"
    assert resolved.fallbacks == ()


def test_override_object_without_profile_falls_through_to_default() -> None:
    profiles = {
        **_FALLBACK_PROFILES,
        "agent_assignments": {
            "default_profile": "claude-primary",
            "overrides": {"coder": {"fallback": ["codex-fallback"]}},
        },
    }

    resolved = resolve_backend_profile(
        backend_profiles=profiles,
        node_id="coder",
        agent_id="agent-a",
        agent_name="Agent A",
        agent_role="role-a",
        base_runtime_id="api-call",
        base_runtime_config={},
        env_layers={},
    )

    assert resolved.profile_id == "claude-primary"
    assert [f.profile_id for f in resolved.fallbacks] == ["codex-fallback"]


def test_unknown_fallback_ids_are_reported() -> None:
    profiles = {
        **_FALLBACK_PROFILES,
        "agent_assignments": {
            "overrides": {
                "coder": {"profile": "claude-primary", "fallback": ["nope", "codex-fallback"]},
                "tester": {"profile": "claude-primary", "fallback": ["also-missing"]},
                "plain": "claude-primary",
            }
        },
    }

    assert fallback_chain_errors(profiles) == [
        "backend_profiles.agent_assignments.overrides.coder.fallback: unknown profile id 'nope'",
        "backend_profiles.agent_assignments.overrides.tester.fallback: "
        "unknown profile id 'also-missing'",
    ]


def test_valid_or_absent_fallbacks_have_no_errors() -> None:
    assert fallback_chain_errors(_FALLBACK_PROFILES) == []
    assert fallback_chain_errors(None) == []
    assert fallback_chain_errors({"agent_assignments": {"default_profile": "x"}}) == []


def test_malformed_fallback_is_an_error() -> None:
    profiles = {
        **_FALLBACK_PROFILES,
        "agent_assignments": {"overrides": {"coder": {"profile": "x", "fallback": "codex"}}},
    }

    assert fallback_chain_errors(profiles) == [
        "backend_profiles.agent_assignments.overrides.coder.fallback: "
        "must be a list of profile ids",
    ]
