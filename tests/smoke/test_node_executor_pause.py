"""Tests for node-initiated pause via __pause sentinel.

Verifies that a python-func node returning ``{"__pause": True}`` causes the
engine to pause the run (via ``PauseRequestedError``), and that the run can
be resumed via ``POST /runs/{id}/resume``.
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

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 5.0


class PauseAdapter:
    """Adapter that returns pause_requested=True on the first call, then
    succeeds normally on subsequent calls (simulating a gate that opens)."""

    id = "pause-stub"
    display_name = "Pause Stub"
    kind: RuntimeKind = "api"

    def __init__(self) -> None:
        self.call_count = 0

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.call_count += 1
        if self.call_count == 1:
            return RuntimeResult(
                success=True,
                output="pausing",
                duration_ms=1,
                structured={"state_delta": {"gate_data": "from_gate"}, "audit": {}},
                pause_requested=True,
            )
        return RuntimeResult(
            success=True,
            output="ok",
            duration_ms=1,
        )


class NeverPauseAdapter:
    """Adapter that never pauses — baseline for comparison."""

    id = "no-pause-stub"
    display_name = "No Pause Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(success=True, output="ok", duration_ms=1)


def _wait_for_status(
    client: TestClient,
    run_id: str,
    target_statuses: set[str],
    timeout_s: float = POLL_TIMEOUT_S,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["final_status"] in target_statuses:
            return body  # type: ignore[no-any-return]
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not reach {target_statuses} within {timeout_s}s")


@pytest.fixture
def pause_sentinel_client() -> Iterator[tuple[TestClient, PauseAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-pause-sentinel-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    adapter = PauseAdapter()
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(adapter)
        yield c, adapter


def _create_agent(client: TestClient, runtime_id: str = "pause-stub") -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Pause Sentinel Test",
            "role": "task_selector",
            "runtime_id": runtime_id,
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str) -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": "Pause Sentinel Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "gate",
            "nodes": [
                {"id": "gate", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
            ],
            "edges": [
                {"id": "e_end", "source": "gate", "target": "__end__"},
            ],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_node_executor_pauses_run_on_pause_requested(
    pause_sentinel_client: tuple[TestClient, PauseAdapter],
) -> None:
    """A node returning pause_requested=True causes the run to pause."""
    client, adapter = pause_sentinel_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    paused = _wait_for_status(client, run_id, {"paused", "success", "failed"})
    assert paused["final_status"] == "paused"
    assert paused["ended_at"] is None
    assert adapter.call_count == 1


def test_resume_after_pause_sentinel_continues_from_gate_node(
    pause_sentinel_client: tuple[TestClient, PauseAdapter],
) -> None:
    """After a pause-sentinel halt, POST /runs/{id}/resume resumes and completes."""
    client, adapter = pause_sentinel_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    _wait_for_status(client, run_id, {"paused"})
    assert adapter.call_count == 1

    # Resume — gate node runs again, this time adapter returns normally
    resume_response = client.post(f"/runs/{run_id}/resume")
    assert resume_response.status_code == 200

    completed = _wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"
    assert completed["ended_at"] is not None
    assert adapter.call_count == 2


def test_pause_sentinel_not_persisted_in_pipeline_state(
    pause_sentinel_client: tuple[TestClient, PauseAdapter],
) -> None:
    """__pause never appears in the committed PipelineState or extensions."""
    client, adapter = pause_sentinel_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    _wait_for_status(client, run_id, {"paused"})

    # Check state snapshots — __pause must not be in state or extensions
    state_history = client.get(f"/runs/{run_id}/state/history").json()
    for snapshot in state_history:
        state = snapshot["state"]
        assert "__pause" not in state
        assert "__pause" not in state.get("extensions", {})

    # Resume and check final state too
    client.post(f"/runs/{run_id}/resume")
    completed = _wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    final_state = client.get(f"/runs/{run_id}/state").json()
    assert "__pause" not in final_state
    assert "__pause" not in final_state.get("extensions", {})
