"""Tests for ``GET /pipelines/{id}/export`` + ``POST /pipelines/import`` (#124).

Mirrors the structure of ``test_agent_import_export.py``. Round-trip
plus the full set of 422 paths (extra field, wrong schema_version,
unknown agent reference, archived agent reference, broken DAG)
plus the 404 export path.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory(prefix="dap-pipeline-export-") as tmp:
        config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
        app = create_app(config)
        with TestClient(app) as c:
            yield c


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
    assert body["schema_version"] == "pipeline-export/1"
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
