"""CRUD tests for /projects endpoints (#63)."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-crud-projects-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _project_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Demo Project",
        "description": "Sample workspace",
        "working_directory": "/tmp/demo",
        "repo_url": None,
        "default_branch": "main",
        "pipelines": {},
        "env_vars": {"WORKSPACE_NAME": "demo"},
    }
    payload.update(overrides)
    return payload


def _seed_pipeline(client: TestClient, name: str = "Demo Pipeline") -> str:
    """Create a minimal one-node pipeline so we have a real id to bind."""
    agent = client.post(
        "/agents",
        json={
            "name": "Stub",
            "role": "task_selector",
            "runtime_id": "api-call",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    ).json()
    pipeline = client.post(
        "/pipelines",
        json={
            "name": name,
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent["id"], "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    ).json()
    return str(pipeline["id"])


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_create_project_returns_201(client: TestClient) -> None:
    response = client.post("/projects", json=_project_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Demo Project"
    assert body["description"] == "Sample workspace"
    assert body["working_directory"] == "/tmp/demo"
    assert body["default_branch"] == "main"
    assert body["pipelines"] == {}
    assert body["env_vars"] == {"WORKSPACE_NAME": "demo"}
    assert body.get("id")
    assert body["archived_at"] is None
    # is_active is a computed field — must round-trip through the API.
    assert body["is_active"] is True
    assert "created_at" in body
    assert "updated_at" in body


def test_create_project_minimal(client: TestClient) -> None:
    """Only ``name`` is required — all other fields default."""
    response = client.post("/projects", json={"name": "Minimal"})
    assert response.status_code == 201
    body = response.json()
    assert body["default_branch"] == "main"
    assert body["pipelines"] == {}
    assert body["env_vars"] == {}
    assert body["working_directory"] is None
    assert body["repo_url"] is None


def test_create_project_with_pipeline_binding(client: TestClient) -> None:
    pipeline_id = _seed_pipeline(client)
    response = client.post(
        "/projects",
        json=_project_payload(pipelines={"develop": pipeline_id}),
    )
    assert response.status_code == 201
    assert response.json()["pipelines"] == {"develop": pipeline_id}


def test_create_project_rejects_unknown_pipeline_binding(
    client: TestClient,
) -> None:
    response = client.post(
        "/projects",
        json=_project_payload(pipelines={"develop": "ghost-pipeline-id"}),
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "ghost-pipeline-id" in detail
    assert "unknown" in detail


def test_create_project_rejects_archived_pipeline_binding(
    client: TestClient,
) -> None:
    pipeline_id = _seed_pipeline(client)
    archive = client.delete(f"/pipelines/{pipeline_id}")
    assert archive.status_code == 204

    response = client.post(
        "/projects",
        json=_project_payload(pipelines={"develop": pipeline_id}),
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert pipeline_id in detail
    assert "archived" in detail


def test_create_project_rejects_extra_field(client: TestClient) -> None:
    response = client.post("/projects", json=_project_payload(unknown="bad"))
    assert response.status_code == 422


def test_create_project_rejects_blank_name(client: TestClient) -> None:
    response = client.post("/projects", json=_project_payload(name=""))
    assert response.status_code == 422


def test_create_project_rejects_blank_pipeline_id(client: TestClient) -> None:
    """Empty/whitespace pipeline id → 422 at request validation."""
    for bad in ("", "   "):
        response = client.post(
            "/projects",
            json=_project_payload(pipelines={"develop": bad}),
        )
        assert response.status_code == 422
        detail = str(response.json()["detail"])
        assert "develop" in detail
        assert "non-blank" in detail


def test_create_project_rejects_blank_kind(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json=_project_payload(pipelines={"   ": "anything"}),
    )
    assert response.status_code == 422
    assert "non-blank" in str(response.json()["detail"])


# --------------------------------------------------------------------------
# Get
# --------------------------------------------------------------------------


def test_get_project_returns_full_record(client: TestClient) -> None:
    pipeline_id = _seed_pipeline(client)
    created = client.post(
        "/projects",
        json=_project_payload(pipelines={"develop": pipeline_id}),
    ).json()

    response = client.get(f"/projects/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["pipelines"] == {"develop": pipeline_id}


def test_get_project_404(client: TestClient) -> None:
    response = client.get("/projects/nonexistent-id")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------


def test_update_project_replaces_fields(client: TestClient) -> None:
    created = client.post("/projects", json=_project_payload()).json()
    project_id = created["id"]
    pipeline_id = _seed_pipeline(client, name="develop pipeline")

    response = client.put(
        f"/projects/{project_id}",
        json=_project_payload(
            name="Renamed",
            description="updated",
            working_directory="/var/work",
            default_branch="develop",
            pipelines={"develop": pipeline_id},
            env_vars={"FLAG": "1"},
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == project_id
    assert body["name"] == "Renamed"
    assert body["description"] == "updated"
    assert body["working_directory"] == "/var/work"
    assert body["default_branch"] == "develop"
    assert body["pipelines"] == {"develop": pipeline_id}
    assert body["env_vars"] == {"FLAG": "1"}
    # updated_at moved forward
    assert body["updated_at"] >= created["updated_at"]


def test_update_project_404(client: TestClient) -> None:
    response = client.put("/projects/missing", json=_project_payload())
    assert response.status_code == 404


def test_update_archived_project_404(client: TestClient) -> None:
    created = client.post("/projects", json=_project_payload()).json()
    project_id = created["id"]
    client.delete(f"/projects/{project_id}")
    response = client.put(f"/projects/{project_id}", json=_project_payload(name="Z"))
    assert response.status_code == 404


def test_update_project_rejects_unknown_pipeline(client: TestClient) -> None:
    project_id = client.post("/projects", json=_project_payload()).json()["id"]
    response = client.put(
        f"/projects/{project_id}",
        json=_project_payload(pipelines={"develop": "ghost"}),
    )
    assert response.status_code == 422
    assert "ghost" in str(response.json()["detail"])


# --------------------------------------------------------------------------
# Archive (DELETE)
# --------------------------------------------------------------------------


def test_archive_project_returns_204_and_hides_from_default_list(
    client: TestClient,
) -> None:
    project_id = client.post("/projects", json=_project_payload()).json()["id"]

    response = client.delete(f"/projects/{project_id}")
    assert response.status_code == 204

    # Default list excludes archived
    listing = client.get("/projects").json()
    assert all(p["id"] != project_id for p in listing["items"])

    # archived=true includes it, with archived_at populated and
    # is_active=False (computed field round-trips through serialization)
    archived_listing = client.get("/projects?archived=true").json()
    matched = [p for p in archived_listing["items"] if p["id"] == project_id]
    assert len(matched) == 1
    assert matched[0]["archived_at"] is not None
    assert matched[0]["is_active"] is False


def test_archive_project_404(client: TestClient) -> None:
    response = client.delete("/projects/missing")
    assert response.status_code == 404


def test_archive_project_is_idempotent(client: TestClient) -> None:
    """Calling archive twice on the same project keeps it archived."""
    project_id = client.post("/projects", json=_project_payload()).json()["id"]
    first = client.delete(f"/projects/{project_id}")
    second = client.delete(f"/projects/{project_id}")
    assert first.status_code == 204
    assert second.status_code == 204


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------


def test_list_projects_paginates(client: TestClient) -> None:
    for i in range(5):
        client.post("/projects", json=_project_payload(name=f"P{i}"))

    listing = client.get("/projects").json()
    assert listing["total"] == 5
    assert len(listing["items"]) == 5

    page = client.get("/projects?limit=2&offset=0").json()
    assert page["limit"] == 2
    assert page["total"] == 5
    assert len(page["items"]) == 2


def test_list_projects_validation(client: TestClient) -> None:
    assert client.get("/projects?limit=0").status_code == 422
    assert client.get("/projects?offset=-1").status_code == 422
