"""Smoke tests for execution_target / execution_commands / execution_env (#611).

Verifies that the three new reserved extension keys are accepted by POST /runs
and survive round-trip through persistence into initial_state.extensions.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 5.0


def _wait_for_completion(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        if body["final_status"] != "running":
            return body
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not complete within {POLL_TIMEOUT_S}s")


class ExecTargetStubAdapter:
    id = "exec-target-stub"
    display_name = "Exec Target Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(success=True, output="ok", duration_ms=1)


@pytest.fixture
def client_with_stub() -> Iterator[tuple[TestClient, RuntimeRegistry]]:
    tmp = tempfile.mkdtemp(prefix="dap-exec-target-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        stub = ExecTargetStubAdapter()
        registry.register(stub)
        yield c, registry


def _create_agent(client: TestClient) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Exec Target Test Agent",
            "role": "task_selector",
            "runtime_id": "exec-target-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str) -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": "Exec Target Pipeline",
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


def test_execution_target_keys_survive_round_trip(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """POST /runs with all three execution_target keys; assert they survive in
    initial_state.extensions after persistence."""
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    extensions = {
        "execution_target": "dixter-pc",
        "execution_commands": [
            "cd /home/dixter/Projects/news-sentiment && python benchmark.py",
            "alembic upgrade head",
        ],
        "execution_env": {
            "PYTHONPATH": "/home/dixter/Projects/news-sentiment",
        },
    }

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {
                "repo": "demo",
                "branch": "main",
                "extensions": extensions,
            },
        },
    )
    assert response.status_code == 201
    run_id = response.json()["id"]

    completed = _wait_for_completion(client, run_id)
    assert completed["final_status"] == "success"

    # extensions must survive persistence via initial_state on the run record
    stored_extensions: dict[str, Any] = completed["initial_state"].get("extensions") or {}
    assert stored_extensions["execution_target"] == "dixter-pc"
    assert stored_extensions["execution_commands"] == [
        "cd /home/dixter/Projects/news-sentiment && python benchmark.py",
        "alembic upgrade head",
    ]
    assert stored_extensions["execution_env"] == {
        "PYTHONPATH": "/home/dixter/Projects/news-sentiment",
    }

    # Also verify they appear in the state snapshot history
    history = client.get(f"/runs/{run_id}/state/history").json()
    assert len(history) >= 1
    first_snapshot_extensions: dict[str, Any] = history[0].get("state", {}).get("extensions") or {}
    assert first_snapshot_extensions["execution_target"] == "dixter-pc"


def test_execution_target_key_alone_accepted(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """execution_target alone (without commands/env) is a valid extension."""
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {
                "repo": "demo",
                "branch": "main",
                "extensions": {"execution_target": "remote-host"},
            },
        },
    )
    assert response.status_code == 201
    run_id = response.json()["id"]
    completed = _wait_for_completion(client, run_id)
    assert completed["final_status"] == "success"

    stored_extensions: dict[str, Any] = completed["initial_state"].get("extensions") or {}
    assert stored_extensions["execution_target"] == "remote-host"


def test_execution_commands_non_list_rejected(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """execution_commands must be a list; a bare string is rejected with 422."""
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {
                "repo": "demo",
                "branch": "main",
                "extensions": {
                    "execution_target": "host",
                    "execution_commands": "not-a-list",
                },
            },
        },
    )
    assert response.status_code == 422
    assert "execution_commands" in response.text


def test_execution_target_absent_leaves_existing_runs_unaffected(
    client_with_stub: tuple[TestClient, RuntimeRegistry],
) -> None:
    """A standard trigger without execution_target keys completes normally."""
    client, _registry = client_with_stub
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {"repo": "demo", "branch": "main"},
        },
    )
    assert response.status_code == 201
    run_id = response.json()["id"]
    completed = _wait_for_completion(client, run_id)
    assert completed["final_status"] == "success"

    stored_extensions: dict[str, Any] = completed["initial_state"].get("extensions") or {}
    assert "execution_target" not in stored_extensions
    assert "execution_commands" not in stored_extensions
    assert "execution_env" not in stored_extensions
