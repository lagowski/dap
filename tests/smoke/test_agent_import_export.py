"""Tests for ``GET /agents/{id}/export`` and ``POST /agents/import`` (#94).

Round-trip + the full set of 422 paths (extra field, unknown
runtime_id, unknown PipelineState field in input_schema,
schema_version mismatch).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    """Authed TestClient shim — see ``tests/smoke/conftest.py::authed_client``.

    Kept as a local alias because most test bodies in this file already
    take a ``client: TestClient`` parameter; renaming them all would
    bloat the X1 diff without changing behaviour.
    """
    return authed_client


def _agent_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Round Trip",
        "role": "test_author",
        "runtime_id": "api-call",
        "runtime_config": {"provider": "anthropic", "model_id": "claude-haiku-4-5"},
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": ["available_issues"],
        "output_schema": ["test_files", "tests_generated"],
        "constraints": ["no_implementation"],
        "budget_limit_usd": 2.5,
        "timeout_ms": 30000,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_returns_portable_shape(client: TestClient) -> None:
    created = client.post("/agents", json=_agent_payload()).json()
    response = client.get(f"/agents/{created['id']}/export")
    assert response.status_code == 200

    body = response.json()
    assert body["schema_version"] == "agent-export/1"
    agent = body["agent"]
    # Per-installation fields stripped
    assert "id" not in agent
    assert "version" not in agent
    assert "created_at" not in agent
    assert "updated_at" not in agent
    assert "is_active" not in agent
    assert "archived_at" not in agent
    # Portable fields all present
    assert agent["name"] == "Round Trip"
    assert agent["role"] == "test_author"
    assert agent["runtime_id"] == "api-call"
    assert agent["runtime_config"] == {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5",
    }
    assert agent["input_schema"] == ["available_issues"]
    assert agent["output_schema"] == ["test_files", "tests_generated"]
    assert agent["constraints"] == ["no_implementation"]
    assert agent["budget_limit_usd"] == 2.5
    assert agent["timeout_ms"] == 30000


def test_export_404_unknown_agent(client: TestClient) -> None:
    response = client.get("/agents/missing-id/export")
    assert response.status_code == 404


def test_export_redacts_secret_like_keys(client: TestClient) -> None:
    """Belt-and-suspenders: literal credentials in runtime_config get scrubbed.

    Secrets are supposed to come from env at runtime, but nothing in the
    schema enforces it — guard against the foot-gun where someone exports
    an agent that has a literal API key sitting in runtime_config.
    """
    payload = _agent_payload(
        runtime_config={
            "provider": "anthropic",
            "model_id": "claude-haiku-4-5",
            "api_key": "sk-ant-live-secret-XYZ",
            "auth_token": "bearer-XYZ",
            "nested": {"openai_api_key": "sk-live-secret"},
        }
    )
    created = client.post("/agents", json=payload).json()

    body = client.get(f"/agents/{created['id']}/export").json()
    runtime = body["agent"]["runtime_config"]
    assert runtime["provider"] == "anthropic"
    assert runtime["model_id"] == "claude-haiku-4-5"
    assert runtime["api_key"] == "<redacted>"
    assert runtime["auth_token"] == "<redacted>"
    assert runtime["nested"]["openai_api_key"] == "<redacted>"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_all_portable_fields(client: TestClient) -> None:
    """Export then import — fields land on the new agent with v1."""
    created = client.post("/agents", json=_agent_payload()).json()
    exported = client.get(f"/agents/{created['id']}/export").json()

    response = client.post("/agents/import", json=exported)
    assert response.status_code == 201
    imported = response.json()

    assert imported["id"] != created["id"]  # new agent
    assert imported["version"] == 1
    assert imported["name"] == created["name"]
    assert imported["role"] == created["role"]
    assert imported["runtime_id"] == created["runtime_id"]
    assert imported["runtime_config"] == created["runtime_config"]
    assert imported["prompt_template"] == created["prompt_template"]
    assert imported["input_schema"] == created["input_schema"]
    assert imported["output_schema"] == created["output_schema"]
    assert imported["constraints"] == created["constraints"]
    assert imported["budget_limit_usd"] == created["budget_limit_usd"]
    assert imported["timeout_ms"] == created["timeout_ms"]


# ---------------------------------------------------------------------------
# Import 422 paths
# ---------------------------------------------------------------------------


def test_import_rejects_missing_schema_version(client: TestClient) -> None:
    response = client.post("/agents/import", json={"agent": _agent_payload()})
    assert response.status_code == 422


def test_import_rejects_wrong_schema_version(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/99-bogus",
            "agent": _agent_payload(),
        },
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "schema_version" in detail
    assert "agent-export/1" in detail


def test_import_rejects_extra_top_level_field(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(),
            "unexpected": "field",
        },
    )
    assert response.status_code == 422


def test_import_rejects_extra_agent_field(client: TestClient) -> None:
    bad = _agent_payload()
    bad["unexpected"] = "field"
    response = client.post(
        "/agents/import",
        json={"schema_version": "agent-export/1", "agent": bad},
    )
    assert response.status_code == 422


def test_import_rejects_unknown_runtime_id(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(runtime_id="ghost-runtime"),
        },
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "ghost-runtime" in detail
    assert "no adapter" in detail


def test_import_rejects_unknown_field_in_input_schema(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(input_schema=["definitely_not_a_field"]),
        },
    )
    assert response.status_code == 422
    assert "definitely_not_a_field" in str(response.json()["detail"])


def test_import_rejects_unknown_field_in_output_schema(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(output_schema=["bogus_output"]),
        },
    )
    assert response.status_code == 422
    assert "bogus_output" in str(response.json()["detail"])


def test_import_rejects_blank_name(client: TestClient) -> None:
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(name=""),
        },
    )
    assert response.status_code == 422


def test_import_rejects_zero_timeout(client: TestClient) -> None:
    """``timeout_ms`` must be > 0 — same constraint as ``AgentCreate``."""
    response = client.post(
        "/agents/import",
        json={
            "schema_version": "agent-export/1",
            "agent": _agent_payload(timeout_ms=0),
        },
    )
    assert response.status_code == 422
