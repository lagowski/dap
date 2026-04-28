"""E2E tests for POST /projects/{project_id}/run/{kind} (#66).

Reuses the same TriggerStubAdapter from test_runs_trigger so we can
introspect the RuntimeTask the adapter was called with — proves
project context (working_directory, env_vars) flows through the
convenience trigger same as /runs.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from fastapi.testclient import TestClient

from tests.smoke.test_runs_trigger import TriggerStubAdapter, _wait_for_completion


@pytest.fixture
def client_with_stub() -> Iterator[tuple[TestClient, RuntimeRegistry, TriggerStubAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-project-run-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        stub = TriggerStubAdapter()
        registry.register(stub)
        yield c, registry, stub


def _create_agent(client: TestClient) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Trigger Test",
            "role": "task_selector",
            "runtime_id": "trigger-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str, name: str = "Pipe") -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": name,
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_project(
    client: TestClient,
    *,
    pipelines: dict[str, str] | None = None,
    repo_url: str | None = None,
    default_branch: str = "main",
    working_directory: str | None = None,
    env_vars: dict[str, str] | None = None,
    name: str = "Demo",
) -> str:
    response = client.post(
        "/projects",
        json={
            "name": name,
            "working_directory": working_directory,
            "repo_url": repo_url,
            "default_branch": default_branch,
            "pipelines": pipelines or {},
            "env_vars": env_vars or {},
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_trigger_bound_kind_starts_run_with_project_context(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Bound kind → 201 Run, run.project_id stamped, adapter sees project cwd + env."""
    client, _registry, stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(
        client,
        pipelines={"develop": pipeline_id},
        working_directory="/tmp/proj-66",
        env_vars={"DAP_PROJ_FLAG": "on"},
    )

    response = client.post(f"/projects/{project_id}/run/develop", json={"initial_state": {}})
    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == project_id
    assert body["pipeline_id"] == pipeline_id
    assert body["final_status"] == "running"
    assert body["trigger_source"] == "api"

    _wait_for_completion(client, body["id"])
    task = stub.received_tasks[-1]
    assert task.working_directory == "/tmp/proj-66"
    assert task.project_env_vars == {"DAP_PROJ_FLAG": "on"}


def test_trigger_seeds_repo_and_branch_from_project(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Project ``repo_url`` + ``default_branch`` land in initial_state when caller omits them."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(
        client,
        pipelines={"plan": pipeline_id},
        repo_url="https://example.com/proj.git",
        default_branch="develop",
    )

    response = client.post(f"/projects/{project_id}/run/plan", json={})
    assert response.status_code == 201
    run_id = response.json()["id"]
    _wait_for_completion(client, run_id)

    fetched = client.get(f"/runs/{run_id}").json()
    assert fetched["initial_state"]["repo"] == "https://example.com/proj.git"
    assert fetched["initial_state"]["branch"] == "develop"


def test_caller_initial_state_overrides_project_defaults(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Caller supplied branch wins over project ``default_branch``."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(
        client,
        pipelines={"verify": pipeline_id},
        repo_url="https://example.com/proj.git",
        default_branch="develop",
    )

    response = client.post(
        f"/projects/{project_id}/run/verify",
        json={"initial_state": {"branch": "feature/x"}},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]
    _wait_for_completion(client, run_id)

    fetched = client.get(f"/runs/{run_id}").json()
    assert fetched["initial_state"]["branch"] == "feature/x"
    # repo still seeded from project (not overridden by caller)
    assert fetched["initial_state"]["repo"] == "https://example.com/proj.git"


def test_pipeline_version_forwarded(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Caller's ``pipeline_version`` flows through to the resolved Run."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    # Bump to v2.
    update = client.put(
        f"/pipelines/{pipeline_id}",
        json={
            "name": "Pipe v2",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert update.status_code == 200

    project_id = _create_project(client, pipelines={"develop": pipeline_id})

    # Default → v2
    default_run = client.post(f"/projects/{project_id}/run/develop", json={}).json()
    assert default_run["pipeline_version"] == 2

    # Explicit v1
    v1_run = client.post(
        f"/projects/{project_id}/run/develop",
        json={"pipeline_version": 1},
    ).json()
    assert v1_run["pipeline_version"] == 1


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_trigger_unknown_kind_returns_404_with_bound_list(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(
        client,
        pipelines={"develop": pipeline_id, "plan": pipeline_id},
    )

    response = client.post(f"/projects/{project_id}/run/release", json={})
    assert response.status_code == 404
    detail = str(response.json()["detail"])
    assert "release" in detail
    # Bound kinds must be listed so the user knows what's actually available.
    assert "develop" in detail
    assert "plan" in detail


def test_trigger_no_bindings_returns_404_with_empty_list(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    client, _registry, _stub = client_with_stub
    project_id = _create_project(client, pipelines={})

    response = client.post(f"/projects/{project_id}/run/develop", json={})
    assert response.status_code == 404
    detail = str(response.json()["detail"])
    assert "develop" in detail
    assert "(none)" in detail


def test_trigger_unknown_project_returns_404(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    client, _registry, _stub = client_with_stub
    response = client.post("/projects/ghost/run/develop", json={})
    assert response.status_code == 404
    assert "Project not found" in str(response.json()["detail"])


def test_trigger_archived_project_returns_409(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Spec says 409 Conflict for archived (resource state forbids action)."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client, pipelines={"develop": pipeline_id})

    archive = client.delete(f"/projects/{project_id}")
    assert archive.status_code == 204

    response = client.post(f"/projects/{project_id}/run/develop", json={})
    assert response.status_code == 409
    assert "archived" in str(response.json()["detail"])


def test_trigger_bound_pipeline_archived_returns_404(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Pipeline archived after binding → trigger_run's own 404 surfaces."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client, pipelines={"develop": pipeline_id})

    # Archive the pipeline behind the binding.
    assert client.delete(f"/pipelines/{pipeline_id}").status_code == 204

    response = client.post(f"/projects/{project_id}/run/develop", json={})
    assert response.status_code == 404
    assert "Pipeline not found" in str(response.json()["detail"])


def test_trigger_with_no_body_uses_defaults(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    """Body is optional — POST without one should still kick off a run."""
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client, pipelines={"develop": pipeline_id})

    response = client.post(f"/projects/{project_id}/run/develop")
    assert response.status_code == 201
    assert response.json()["project_id"] == project_id


def test_extra_body_field_rejected(
    client_with_stub: tuple[TestClient, RuntimeRegistry, TriggerStubAdapter],
) -> None:
    client, _registry, _stub = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client, pipelines={"develop": pipeline_id})

    response = client.post(
        f"/projects/{project_id}/run/develop",
        json={"unknown_field": "x"},
    )
    assert response.status_code == 422
