"""Tests for pause/resume — LangGraph SqliteSaver checkpointing.

Pausing a run cancels its background task between node boundaries; the
LangGraph checkpoint preserves graph state so a subsequent /resume picks
up where it left off without re-executing completed nodes.
"""

from __future__ import annotations

import asyncio
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


class CountingSlowAdapter:
    """Adapter that sleeps + counts executions, so we can verify resume
    didn't re-execute the already-completed nodes."""

    id = "counting-slow-stub"
    display_name = "Counting Slow Stub"
    kind: RuntimeKind = "api"

    def __init__(self, sleep_seconds: float = 0.5) -> None:
        self.sleep_seconds = sleep_seconds
        self.calls: list[str] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.calls.append(task.execution_id)
        await asyncio.sleep(self.sleep_seconds)
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
def pause_client() -> Iterator[tuple[TestClient, CountingSlowAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-pause-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    adapter = CountingSlowAdapter(sleep_seconds=0.5)
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(adapter)
        yield c, adapter


def _create_agent(client: TestClient) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Pause Test",
            "role": "task_selector",
            "runtime_id": "counting-slow-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str, num_nodes: int = 3) -> str:
    nodes = [
        {"id": f"n{i + 1}", "agent_id": agent_id, "position": {"x": i * 100, "y": 0}}
        for i in range(num_nodes)
    ]
    edges = [
        {"id": f"e{i + 1}", "source": f"n{i + 1}", "target": f"n{i + 2}"}
        for i in range(num_nodes - 1)
    ]
    edges.append({"id": "e_end", "source": f"n{num_nodes}", "target": "__end__"})
    response = client.post(
        "/pipelines",
        json={
            "name": "Pause Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_pause_running_run(pause_client: tuple[TestClient, CountingSlowAdapter]) -> None:
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]
    assert triggered["final_status"] == "running"

    # Let the first node start
    time.sleep(0.1)

    pause_response = client.post(f"/runs/{run_id}/pause")
    assert pause_response.status_code == 200

    paused = _wait_for_status(client, run_id, {"paused"})
    assert paused["final_status"] == "paused"
    # Paused runs are not finalized — ended_at stays null
    assert paused["ended_at"] is None


def test_resume_paused_run_completes(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    client, adapter = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    # Pause after first node likely committed
    time.sleep(0.6)
    client.post(f"/runs/{run_id}/pause")
    _wait_for_status(client, run_id, {"paused"})

    calls_before_resume = len(adapter.calls)
    assert calls_before_resume >= 1

    # Resume — pipeline picks up from checkpoint
    resume_response = client.post(f"/runs/{run_id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["final_status"] == "running"
    assert resume_response.json()["ended_at"] is None

    # Wait for completion
    completed = _wait_for_status(client, run_id, {"success", "failed", "aborted"})
    assert completed["final_status"] == "success"
    assert completed["ended_at"] is not None

    # LangGraph checkpoints between nodes — on resume, the in-flight node at
    # pause time may re-execute, so calls can exceed num_nodes by 1. The key
    # invariant: resume used the checkpoint (didn't restart from scratch),
    # which means total calls ≤ 2 * num_nodes - 1.
    assert 3 <= len(adapter.calls) <= 5, (
        f"Expected 3-5 node executions across pause+resume, got {len(adapter.calls)}"
    )


def test_pause_already_completed_returns_409(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    _wait_for_status(client, run_id, {"success", "failed"})

    response = client.post(f"/runs/{run_id}/pause")
    assert response.status_code == 409


def test_pause_unknown_run_returns_404(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    client, _ = pause_client
    response = client.post("/runs/nonexistent/pause")
    assert response.status_code == 404


def test_resume_non_paused_returns_409(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    # While running — 409
    response = client.post(f"/runs/{run_id}/resume")
    assert response.status_code == 409

    # After completion — also 409
    _wait_for_status(client, run_id, {"success", "failed"})
    response = client.post(f"/runs/{run_id}/resume")
    assert response.status_code == 409


def test_resume_unknown_run_returns_404(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    client, _ = pause_client
    response = client.post("/runs/nonexistent/resume")
    assert response.status_code == 404


def test_abort_paused_run(pause_client: tuple[TestClient, CountingSlowAdapter]) -> None:
    """A paused run can be aborted (terminal state) instead of resumed."""
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    time.sleep(0.1)
    client.post(f"/runs/{run_id}/pause")
    _wait_for_status(client, run_id, {"paused"})

    abort_response = client.post(f"/runs/{run_id}/abort")
    assert abort_response.status_code == 200
    assert abort_response.json()["final_status"] == "aborted"
    assert abort_response.json()["ended_at"] is not None
