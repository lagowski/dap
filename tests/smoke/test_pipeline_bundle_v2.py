"""Tests for pipeline-export/2 bundle format support (#478).

Covers all four required changes:
1. Accept schema_version "pipeline-export/2"
2. Silently ignore _comment and install_instructions fields
3. Reject if min_dap_version is newer than running engine (422 with clear message)
4. Persist backend_profiles and return it on GET /pipelines/{id}
5. Allow bundled agents with extension fields in input_schema/output_schema
   that don't exist in PipelineState (e.g. issue_number, pr_url, etc.)
6. Existing pipeline-export/1 imports still work (non-regression)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def _create_minimal_agent(client: TestClient, name: str = "Agent") -> str:
    response = client.post(
        "/agents",
        json={
            "name": name,
            "role": "task_selector",
            "runtime_id": "api-call",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _pipeline_payload(agent_id: str, **overrides: Any) -> dict[str, Any]:
    """Single-node pipeline that passes the DAG validator."""
    payload: dict[str, Any] = {
        "name": "V2 Bundle Test Pipeline",
        "description": "pipeline-export/2 smoke tests",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    payload.update(overrides)
    return payload


def _agent_export_payload(name: str = "Bundled Agent", **overrides: Any) -> dict[str, Any]:
    """Portable agent payload for bundled_agents."""
    payload: dict[str, Any] = {
        "name": name,
        "role": "task_selector",
        "runtime_id": "api-call",
        "runtime_config": {"provider": "anthropic", "model_id": "claude-haiku-4-5"},
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": [],
        "output_schema": [],
        "constraints": [],
        "budget_limit_usd": None,
        "timeout_ms": 60_000,
    }
    payload.update(overrides)
    return payload


def _v2_bundle(agent_id: str, **overrides: Any) -> dict[str, Any]:
    """Minimal valid pipeline-export/2 bundle."""
    bundle: dict[str, Any] = {
        "schema_version": "pipeline-export/2",
        "pipeline": _pipeline_payload(agent_id),
    }
    bundle.update(overrides)
    return bundle


# ---------------------------------------------------------------------------
# 1. Accept schema_version "pipeline-export/2"
# ---------------------------------------------------------------------------


def test_v2_bundle_imports_successfully(client: TestClient) -> None:
    """pipeline-export/2 with no extra fields is accepted (201)."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id)
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    body = response.json()
    assert "id" in body
    assert body["name"] == "V2 Bundle Test Pipeline"


# ---------------------------------------------------------------------------
# 2. Silently ignore _comment and install_instructions
# ---------------------------------------------------------------------------


def test_v2_bundle_ignores_comment_field(client: TestClient) -> None:
    """_comment at the top level is silently ignored (not a 422)."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(
        agent_id,
        _comment="This is documentation text that should be ignored.",
    )
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_v2_bundle_ignores_install_instructions(client: TestClient) -> None:
    """install_instructions at the top level is silently ignored (not a 422)."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(
        agent_id,
        install_instructions="Run `dap import cortex.pipeline-bundle.json` to install.",
    )
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_v2_bundle_ignores_both_documentation_fields(client: TestClient) -> None:
    """Both _comment and install_instructions together are silently ignored."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(
        agent_id,
        _comment="Phase 1 pipeline.",
        install_instructions="Import with DAP CLI.",
    )
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_reimport_same_name_bumps_version_under_same_id(client: TestClient) -> None:
    """Re-importing a same-named bundle bumps the version under the existing
    pipeline_id instead of creating a duplicate row (#755)."""
    r1 = client.post("/pipelines/import", json=_v2_bundle(_create_minimal_agent(client)))
    assert r1.status_code == 201, r1.text
    p1 = r1.json()
    assert p1["version"] == 1

    # Re-import the same name with a tweaked definition.
    b2 = _v2_bundle(_create_minimal_agent(client))
    b2["pipeline"]["description"] = "updated on re-import"
    r2 = client.post("/pipelines/import", json=b2)
    assert r2.status_code == 201, r2.text
    p2 = r2.json()
    assert p2["id"] == p1["id"]  # same pipeline, not a duplicate
    assert p2["version"] == 2


def test_import_different_name_creates_a_new_pipeline(client: TestClient) -> None:
    r1 = client.post("/pipelines/import", json=_v2_bundle(_create_minimal_agent(client)))
    b2 = _v2_bundle(_create_minimal_agent(client))
    b2["pipeline"]["name"] = "A Distinctly Different Pipeline"
    r2 = client.post("/pipelines/import", json=b2)
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] != r1.json()["id"]


def test_reimport_carries_backend_profiles_onto_the_bumped_version(client: TestClient) -> None:
    """The version bump must preserve backend_profiles (cortex relies on them)."""
    client.post("/pipelines/import", json=_v2_bundle(_create_minimal_agent(client)))
    profiles = {"available": {"cheap": {"backend": "api/anthropic"}}, "agent_assignments": {}}
    b2 = _v2_bundle(_create_minimal_agent(client), backend_profiles=profiles)
    r2 = client.post("/pipelines/import", json=b2)
    assert r2.status_code == 201, r2.text
    assert r2.json()["version"] == 2
    assert r2.json()["backend_profiles"] == profiles


def test_v2_bundle_ignores_structured_documentation_fields(client: TestClient) -> None:
    """Real bundles ship _comment / install_instructions as structured blocks,
    not strings — those must be ignored, not 422 with a string_type error (#755)."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(
        agent_id,
        _comment={"author": "cortex", "notes": ["a", "b"]},
        install_instructions={
            "steps": ["pip install dap-cortex", "set CORTEX_DATABASE_URL"],
            "required_env": ["CORTEX_DATABASE_URL"],
        },
    )
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# 3. min_dap_version — version gate
# ---------------------------------------------------------------------------


def test_v2_bundle_min_dap_version_absent_imports_ok(client: TestClient) -> None:
    """No min_dap_version field → import allowed regardless of running version."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id)
    assert "min_dap_version" not in bundle
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_v2_bundle_min_dap_version_lower_than_running_imports_ok(client: TestClient) -> None:
    """min_dap_version older than or equal to running version → import allowed."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id, min_dap_version="0.1.0")
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_v2_bundle_min_dap_version_same_as_running_imports_ok(client: TestClient) -> None:
    """min_dap_version equal to running version → import allowed."""
    agent_id = _create_minimal_agent(client)
    # Engine version is 0.3.0; same version is compatible.
    bundle = _v2_bundle(agent_id, min_dap_version="0.3.0")
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text


def test_v2_bundle_min_dap_version_newer_than_running_rejects_422(client: TestClient) -> None:
    """min_dap_version newer than running engine → 422 with clear message."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id, min_dap_version="999.0.0")
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    detail_str = str(detail)
    # Must mention the required version and the running version
    assert "999.0.0" in detail_str
    assert "DAP" in detail_str or "dap" in detail_str.lower()


# ---------------------------------------------------------------------------
# 4. backend_profiles — persisted and returned
# ---------------------------------------------------------------------------


def test_v2_bundle_backend_profiles_persisted(client: TestClient) -> None:
    """backend_profiles is stored and returned on GET /pipelines/{id}."""
    agent_id = _create_minimal_agent(client)
    profiles = {
        "available": ["anthropic", "openai"],
        "agent_assignments": {"cx_p1_mockup": "anthropic"},
    }
    bundle = _v2_bundle(agent_id, backend_profiles=profiles)
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    pipeline_id = response.json()["id"]

    fetched = client.get(f"/pipelines/{pipeline_id}")
    assert fetched.status_code == 200, fetched.text
    fetched_body = fetched.json()
    assert fetched_body.get("backend_profiles") == profiles


def test_v2_bundle_backend_profiles_absent_returns_none(client: TestClient) -> None:
    """When backend_profiles is absent the field is None/absent on GET."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id)
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    pipeline_id = response.json()["id"]

    fetched = client.get(f"/pipelines/{pipeline_id}")
    assert fetched.status_code == 200, fetched.text
    # Should be absent or None — not a crash
    assert fetched.json().get("backend_profiles") is None


def test_v2_bundle_backend_profiles_inspection_absent_is_empty(
    client: TestClient,
) -> None:
    """Backend inspection is backward-compatible for bundles without profiles."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id)

    response = client.post("/pipelines/import/inspect-backends", json=bundle)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "default_profile": None,
        "overrides": {},
        "profiles": [],
    }


def test_v2_bundle_backend_profiles_inspection_reports_env_and_service(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspection marks profiles available only when requirements are met."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def fake_which(command: str) -> str | None:
        return "/usr/bin/claude" if command == "claude" else None

    monkeypatch.setattr("dap_engine.api.backend_profiles.shutil.which", fake_which)

    agent_id = _create_minimal_agent(client)
    profiles = {
        "available": {
            "claude-api": {
                "label": "Claude API",
                "description": "Uses Anthropic API.",
                "requires_env": ["ANTHROPIC_API_KEY"],
                "requires_service": None,
            },
            "claude-subscription": {
                "label": "Claude Code",
                "requires_env": [],
                "requires_service": "claude-cli",
            },
            "deepseek-api": {
                "label": "DeepSeek API",
                "requires_env": ["DEEPSEEK_API_KEY"],
                "requires_service": None,
            },
            "local-gemma": {
                "label": "Local Ollama",
                "requires_env": [],
                "requires_service": "ollama",
            },
        },
        "agent_assignments": {
            "default_profile": "claude-subscription",
            "overrides": {"coder": "claude-api"},
        },
    }
    bundle = _v2_bundle(agent_id, backend_profiles=profiles)

    response = client.post("/pipelines/import/inspect-backends", json=bundle)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["default_profile"] == "claude-subscription"
    assert body["overrides"] == {"coder": "claude-api"}

    by_id = {profile["id"]: profile for profile in body["profiles"]}
    assert by_id["claude-api"] == {
        "id": "claude-api",
        "label": "Claude API",
        "description": "Uses Anthropic API.",
        "requires_env": ["ANTHROPIC_API_KEY"],
        "missing_env": [],
        "requires_service": None,
        "service_available": None,
        "available": True,
    }
    assert by_id["claude-subscription"]["available"] is True
    assert by_id["claude-subscription"]["requires_service"] == "claude-cli"
    assert by_id["claude-subscription"]["service_available"] is True
    assert by_id["deepseek-api"]["available"] is False
    assert by_id["deepseek-api"]["missing_env"] == ["DEEPSEEK_API_KEY"]
    assert by_id["local-gemma"]["available"] is False
    assert by_id["local-gemma"]["service_available"] is False


# ---------------------------------------------------------------------------
# 5. Extension fields in bundled-agent input_schema/output_schema
# ---------------------------------------------------------------------------


def test_v2_bundle_bundled_agents_with_extension_fields_import_ok(client: TestClient) -> None:
    """Bundled agents may declare extension fields (issue_number, pr_url, etc.)
    in input_schema/output_schema that are not in PipelineState — must not 422."""
    source_agent_id = "cortex-coder-source-id"
    extension_agent = _agent_export_payload(
        name="Cortex Coder",
        input_schema=["issue_url", "issue_number", "repo", "branch"],
        output_schema=["files_changed", "__audit"],
    )
    bundle = {
        "schema_version": "pipeline-export/2",
        "pipeline": _pipeline_payload(source_agent_id),
        "bundled_agents": {source_agent_id: extension_agent},
    }
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    imported = response.json()
    new_agent_id = imported["nodes"][0]["agent_id"]
    assert new_agent_id != source_agent_id

    # Verify the agent was created with the extension schemas preserved
    fetched_agent = client.get(f"/agents/{new_agent_id}")
    assert fetched_agent.status_code == 200
    agent_body = fetched_agent.json()
    assert "issue_number" in agent_body["input_schema"]
    assert "__audit" in agent_body["output_schema"]


def test_v2_bundle_bundled_agent_malformed_extension_field_rejected(
    client: TestClient,
) -> None:
    """Malformed bundled-agent schema refs fail normal AgentCreate validation."""
    source_agent_id = "cortex-coder-source-id"
    bundle = {
        "schema_version": "pipeline-export/2",
        "pipeline": _pipeline_payload(source_agent_id),
        "bundled_agents": {
            source_agent_id: _agent_export_payload(
                name="Cortex Coder",
                input_schema=["extensions."],
            ),
        },
    }
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 422, response.text
    assert "extensions." in str(response.json()["detail"])


def test_v2_bundle_cortex_style_multi_agent_bundle_imports_ok(client: TestClient) -> None:
    """Simulate a Cortex-style bundle with multiple agents using extension fields."""
    # Two-node pipeline with extension-schema agents (mimics cortex full bundle)
    src_coder = "cx_p2_coder"
    src_reviewer = "cx_p2_reviewer"

    two_node_pipeline = {
        "name": "Cortex Phase 2 Mini",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": src_coder, "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": src_reviewer, "position": {"x": 100, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "n2"},
            {"id": "e3", "source": "n2", "target": "__end__"},
        ],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    bundle = {
        "schema_version": "pipeline-export/2",
        "pipeline": two_node_pipeline,
        "_comment": "Cortex Phase 2 pipeline",
        "install_instructions": "dap import ...",
        "min_dap_version": "0.1.0",
        "bundled_agents": {
            src_coder: _agent_export_payload(
                name="Cortex Coder",
                input_schema=["issue_url", "issue_number", "repo", "branch"],
                output_schema=["files_changed", "__audit"],
            ),
            src_reviewer: _agent_export_payload(
                name="Cortex Reviewer",
                input_schema=["branch", "files_changed"],
                output_schema=["review_status", "__audit"],
            ),
        },
    }
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert len(imported["nodes"]) == 2


# ---------------------------------------------------------------------------
# 6. Non-regression: pipeline-export/1 still works
# ---------------------------------------------------------------------------


def test_v1_bundle_still_imports_successfully(client: TestClient) -> None:
    """Existing pipeline-export/1 imports are unaffected (non-regression)."""
    agent_id = _create_minimal_agent(client)
    bundle = {
        "schema_version": "pipeline-export/1",
        "pipeline": _pipeline_payload(agent_id),
    }
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "V2 Bundle Test Pipeline"


def test_v1_bundle_with_bundled_agents_still_works(client: TestClient) -> None:
    """Bundle round-trip for pipeline-export/1 with bundled_agents still works."""
    source_id = "v1-bundled-agent-source"
    bundle = {
        "schema_version": "pipeline-export/1",
        "pipeline": _pipeline_payload(source_id),
        "bundled_agents": {source_id: _agent_export_payload(name="V1 Bundled")},
    }
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    imported = response.json()
    new_agent_id = imported["nodes"][0]["agent_id"]
    assert new_agent_id != source_id
    fetched = client.get(f"/agents/{new_agent_id}")
    assert fetched.json()["name"] == "V1 Bundled"


def test_wrong_schema_version_still_rejected(client: TestClient) -> None:
    """Completely unknown schema_version is still rejected with 422."""
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/99-bogus",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "schema_version" in detail


def test_v2_extra_unknown_top_level_field_rejected(client: TestClient) -> None:
    """A v2 bundle with a truly unexpected unknown field (not the documented ones)
    is still rejected — we only allow the documented optional fields."""
    agent_id = _create_minimal_agent(client)
    bundle = _v2_bundle(agent_id, totally_unknown_field="surprise")
    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 422
