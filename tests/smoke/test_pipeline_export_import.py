"""Tests for ``GET /pipelines/{id}/export`` + ``POST /pipelines/import`` (#124).

Mirrors the structure of ``test_agent_import_export.py``. Round-trip
plus the full set of 422 paths (extra field, wrong schema_version,
unknown agent reference, archived agent reference, broken DAG)
plus the 404 export path.
"""

from __future__ import annotations

from typing import Any

import pytest
from dap_engine.version import __version__
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    """Authed TestClient shim — see ``tests/smoke/conftest.py::authed_client``.

    Kept as a local alias because most test bodies in this file already
    take a ``client: TestClient`` parameter; renaming them all would
    bloat the X1 diff without changing behaviour.
    """
    return authed_client


def _create_minimal_agent(client: TestClient, name: str = "Agent") -> str:
    """Empty input/output schemas so the DAG validator doesn't trip on
    cohesion gaps regardless of how the test wires nodes/edges."""
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
    """Single-node pipeline that round-trips cleanly through the validator."""
    payload: dict[str, Any] = {
        "name": "Round Trip Pipeline",
        "description": "Pipeline for export/import smoke tests",
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


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_returns_portable_shape(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    created = client.post("/pipelines", json=_pipeline_payload(agent_id)).json()

    response = client.get(f"/pipelines/{created['id']}/export")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["schema_version"] == "pipeline-export/2"
    assert body["min_dap_version"] == __version__
    pipeline = body["pipeline"]
    # Per-installation fields stripped
    assert "id" not in pipeline
    assert "version" not in pipeline
    assert "created_at" not in pipeline
    assert "updated_at" not in pipeline
    assert "is_active" not in pipeline
    assert "archived_at" not in pipeline
    # Portable fields all present and matching
    assert pipeline["name"] == "Round Trip Pipeline"
    assert pipeline["entry_point"] == "n1"
    assert len(pipeline["nodes"]) == 1
    assert pipeline["nodes"][0]["agent_id"] == agent_id
    assert len(pipeline["edges"]) == 2


def test_export_404_unknown_pipeline(client: TestClient) -> None:
    response = client.get("/pipelines/missing-id/export")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_all_portable_fields(client: TestClient) -> None:
    """Export then import in the same DB — fields land on the new pipeline
    with v1 and the agent reference still resolves locally."""
    agent_id = _create_minimal_agent(client)
    created = client.post("/pipelines", json=_pipeline_payload(agent_id)).json()
    exported = client.get(f"/pipelines/{created['id']}/export").json()

    response = client.post("/pipelines/import", json=exported)
    assert response.status_code == 201, response.text
    imported = response.json()

    assert imported["id"] != created["id"]  # new pipeline
    assert imported["version"] == 1
    assert imported["name"] == created["name"]
    assert imported["description"] == created["description"]
    assert imported["entry_point"] == created["entry_point"]
    assert len(imported["nodes"]) == len(created["nodes"])
    assert imported["nodes"][0]["agent_id"] == agent_id
    assert len(imported["edges"]) == len(created["edges"])


# ---------------------------------------------------------------------------
# Import 422 paths
# ---------------------------------------------------------------------------


def test_import_rejects_missing_schema_version(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={"pipeline": _pipeline_payload(agent_id)},
    )
    assert response.status_code == 422


def test_import_rejects_wrong_schema_version(client: TestClient) -> None:
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
    assert "pipeline-export/1" in detail
    assert "pipeline-export/2" in detail


def test_import_allows_legacy_bundle_without_min_dap_version(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/1",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 201, response.text


def test_import_rejects_bundle_that_requires_newer_dap(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/2",
            "min_dap_version": "999.0.0",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Bundle requires DAP >= 999.0.0" in detail
    assert f"this instance is {__version__}" in detail
    assert "Update DAP before importing this bundle" in detail


def test_import_accepts_bundle_when_min_dap_version_is_met(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/2",
            "min_dap_version": __version__,
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 201, response.text


def test_import_accepts_prerelease_min_dap_version(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/2",
            "min_dap_version": "0.3.0-rc1",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 201, response.text


def test_import_rejects_invalid_min_dap_version(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/2",
            "min_dap_version": "next",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 422
    assert "Invalid min_dap_version" in response.json()["detail"]


def test_import_rejects_extra_top_level_field(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/1",
            "pipeline": _pipeline_payload(agent_id),
            "unexpected": "field",
        },
    )
    assert response.status_code == 422


def test_import_rejects_extra_pipeline_field(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    bad = _pipeline_payload(agent_id)
    bad["unexpected"] = "field"
    response = client.post(
        "/pipelines/import",
        json={"schema_version": "pipeline-export/1", "pipeline": bad},
    )
    assert response.status_code == 422


def test_import_rejects_unknown_agent_reference(client: TestClient) -> None:
    """The DAG validator's ``_check_agents`` (#120) catches the
    cross-installation case: imported pipeline references an agent that
    doesn't exist in the target DB."""
    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/1",
            "pipeline": _pipeline_payload("ghost-agent-id"),
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # detail is a dict {errors: [...], warnings: [...]} from #120's
    # _enforce_validation helper.
    assert "errors" in detail
    assert any("ghost-agent-id" in err for err in detail["errors"])


def test_import_rejects_archived_agent_reference(client: TestClient) -> None:
    agent_id = _create_minimal_agent(client)
    # Archive the agent — same validator path that catches "missing"
    # also catches "archived" (#120).
    archived_response = client.delete(f"/agents/{agent_id}")
    assert archived_response.status_code == 204

    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/1",
            "pipeline": _pipeline_payload(agent_id),
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "errors" in detail
    assert any("archived" in err.lower() for err in detail["errors"])


# ---------------------------------------------------------------------------
# Bundle export + import (#126)
# ---------------------------------------------------------------------------


def _agent_export_payload(name: str = "Bundled Agent") -> dict[str, Any]:
    """Portable agent shape compatible with ``AgentExportPayload``."""
    return {
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


def test_export_bundle_includes_referenced_agents(client: TestClient) -> None:
    """``?bundle=true`` adds ``bundled_agents`` keyed by agent_id;
    plain export omits the field entirely (Phase 1 wire shape)."""
    agent_id = _create_minimal_agent(client, name="My Source Agent")
    pipeline = client.post("/pipelines", json=_pipeline_payload(agent_id)).json()

    plain = client.get(f"/pipelines/{pipeline['id']}/export").json()
    # Field is omitted, not null — ``response_model_exclude_none``
    # keeps Phase 1 importers blind to the new field.
    assert "bundled_agents" not in plain

    bundled = client.get(f"/pipelines/{pipeline['id']}/export?bundle=true").json()
    assert "bundled_agents" in bundled
    assert agent_id in bundled["bundled_agents"]
    bundled_agent = bundled["bundled_agents"][agent_id]
    assert bundled_agent["name"] == "My Source Agent"
    # Per-installation fields stripped (same as standalone agent export).
    assert "id" not in bundled_agent
    assert "version" not in bundled_agent


def test_export_bundle_scrubs_secrets_in_runtime_config(client: TestClient) -> None:
    """Bundle reuses ``build_agent_export_payload`` so the secret-key
    redaction from #94 applies to bundled agents too."""
    create_response = client.post(
        "/agents",
        json={
            "name": "Has Secret",
            "role": "task_selector",
            "runtime_id": "api-call",
            "runtime_config": {
                "model_id": "claude-haiku-4-5",
                "max_tokens": 1024,
                "api_key": "sk-very-secret",
            },
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert create_response.status_code == 201, create_response.text
    agent_id = create_response.json()["id"]
    pipeline = client.post("/pipelines", json=_pipeline_payload(agent_id)).json()

    bundled = client.get(f"/pipelines/{pipeline['id']}/export?bundle=true").json()
    bundled_config = bundled["bundled_agents"][agent_id]["runtime_config"]
    assert bundled_config["max_tokens"] == 1024
    assert bundled_config["api_key"] == "<redacted>"


def test_bundle_import_creates_agents_and_remaps_node_references(
    client: TestClient,
) -> None:
    """End-to-end: import a bundle into a fresh DB-state. Bundled
    agents land as new rows; pipeline's ``node.agent_id`` strings
    point at the freshly assigned local ids, not the source ones."""
    # Build a bundle by hand so we can prove remapping happens
    # (no agent with this id exists in the DB yet).
    source_agent_id = "source-old-id-doesnt-exist-locally"
    bundle = {
        "schema_version": "pipeline-export/1",
        "pipeline": _pipeline_payload(source_agent_id),
        "bundled_agents": {source_agent_id: _agent_export_payload()},
    }

    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 201, response.text
    imported = response.json()

    # Pipeline's node.agent_id should NOT match the source id any more —
    # the importer rewrote it to whatever local id the new agent got.
    new_agent_id = imported["nodes"][0]["agent_id"]
    assert new_agent_id != source_agent_id

    # The new agent exists, has the bundled name, and the imported
    # pipeline points at it.
    fetched = client.get(f"/agents/{new_agent_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Bundled Agent"


def test_bundle_import_rolls_back_agents_on_pipeline_validation_failure(
    client: TestClient,
) -> None:
    """Transactional invariant: if pipeline validation fails, the
    bundled agents must NOT survive — orphans would be a real bug."""
    source_agent_id = "phantom-agent"
    # Construct a bundle where the agents would land but the pipeline
    # references a node id that doesn't match the entry_point — DAG
    # validation rejects this. The bundled agent should not stick.
    bundle = {
        "schema_version": "pipeline-export/1",
        "pipeline": _pipeline_payload(
            source_agent_id,
            entry_point="nonexistent_node",  # triggers validator
        ),
        "bundled_agents": {source_agent_id: _agent_export_payload(name="Should Not Persist")},
    }

    response = client.post("/pipelines/import", json=bundle)
    assert response.status_code == 422

    # No agent with the bundled name should be in the DB.
    listing = client.get("/agents").json()
    names = {item["name"] for item in listing["items"]}
    assert "Should Not Persist" not in names


def test_bundle_round_trip(client: TestClient) -> None:
    """Export with ``bundle=true`` → import same envelope → fresh
    pipeline + fresh agents in a single round-trip. The imported
    pipeline should still validate (no missing references)."""
    source_agent_id = _create_minimal_agent(client, name="Round Trip Source")
    created = client.post("/pipelines", json=_pipeline_payload(source_agent_id)).json()

    exported = client.get(f"/pipelines/{created['id']}/export?bundle=true").json()
    assert exported["bundled_agents"] is not None

    response = client.post("/pipelines/import", json=exported)
    assert response.status_code == 201, response.text
    imported = response.json()

    # Two distinct pipelines, two distinct agent rows (since the
    # importer always creates fresh; conflict resolution against
    # existing names is out of scope per the issue).
    assert imported["id"] != created["id"]
    new_agent_id = imported["nodes"][0]["agent_id"]
    assert new_agent_id != source_agent_id


def test_bundle_import_partial_bundle_uses_local_agent_for_unbundled_ref(
    client: TestClient,
) -> None:
    """A pipeline with two nodes where only one agent is bundled and
    the other already exists locally should land successfully — the
    importer remaps the bundled one and leaves the unbundled
    reference alone."""
    local_agent_id = _create_minimal_agent(client, name="Local Existing")
    source_bundled_id = "source-bundled-id"

    pipeline_payload = {
        "name": "Two Node Pipeline",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [
            {"id": "n1", "agent_id": source_bundled_id, "position": {"x": 0, "y": 0}},
            {"id": "n2", "agent_id": local_agent_id, "position": {"x": 100, "y": 0}},
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

    response = client.post(
        "/pipelines/import",
        json={
            "schema_version": "pipeline-export/1",
            "pipeline": pipeline_payload,
            "bundled_agents": {source_bundled_id: _agent_export_payload()},
        },
    )
    assert response.status_code == 201, response.text
    imported = response.json()

    nodes_by_id = {n["id"]: n for n in imported["nodes"]}
    # n1's agent_id was remapped from the source id to a fresh local id.
    assert nodes_by_id["n1"]["agent_id"] != source_bundled_id
    # n2's agent_id wasn't in the bundle so it stayed as the
    # original local id — proving the importer doesn't touch
    # references that aren't in the bundle.
    assert nodes_by_id["n2"]["agent_id"] == local_agent_id


def test_import_rejects_broken_dag(client: TestClient) -> None:
    """A payload that's structurally valid Pydantic but has a duplicate
    node id should fail the DAG validator's ``_check_duplicate_ids``."""
    agent_id = _create_minimal_agent(client)
    bad = _pipeline_payload(
        agent_id,
        nodes=[
            {"id": "dup", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
            {"id": "dup", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
        ],
        edges=[
            {"id": "e1", "source": "__start__", "target": "dup"},
            {"id": "e2", "source": "dup", "target": "__end__"},
        ],
        entry_point="dup",
    )
    response = client.post(
        "/pipelines/import",
        json={"schema_version": "pipeline-export/1", "pipeline": bad},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "errors" in detail
    assert any("duplicate" in err.lower() for err in detail["errors"])
