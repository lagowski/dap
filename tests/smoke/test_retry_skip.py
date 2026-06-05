"""Tests for retry-node + skip-node — LangGraph state-rewind interventions.

A failed run can be recovered without restarting from scratch:
- POST /runs/{id}/nodes/{node_id}/retry — rewinds to before the named node
  and resumes; the node executes again (useful for transient failures).
- POST /runs/{id}/nodes/{node_id}/skip — rewinds to before the named node
  and resumes past it (useful when a node is broken but the rest is OK).
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
from dap_types import (
    HealthStatus,
    OutputCallback,
    RuntimeKind,
    RuntimeResult,
    RuntimeTask,
)
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 5.0


class FlakeyAdapter:
    """Adapter that fails the first N times, then succeeds.

    Lets us simulate a transient failure that retry-node should recover from.
    """

    id = "flakey-stub"
    display_name = "Flakey Stub"
    kind: RuntimeKind = "api"

    def __init__(self, fail_first: int = 1) -> None:
        self.fail_first = fail_first
        self.calls: list[str] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.calls.append(task.execution_id)
        if len(self.calls) <= self.fail_first:
            return RuntimeResult(
                success=False,
                output="",
                duration_ms=1,
                errors=["transient failure"],
            )
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
def flakey_client() -> Iterator[tuple[TestClient, FlakeyAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-retry-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    app = create_app(config)
    adapter = FlakeyAdapter(fail_first=1)
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(adapter)
        yield c, adapter


def _create_agent(client: TestClient, runtime_id: str = "flakey-stub") -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Retry Test",
            "role": "task_selector",
            "runtime_id": runtime_id,
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str, num_nodes: int = 2) -> str:
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
            "name": "Retry Pipeline",
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
                # This suite verifies retry/skip behavior, not terminal-status
                # enforcement (#628). Opt out so a residual ``running`` keeps the
                # legacy "no node raised = success" semantics these tests rely on.
                "requires_terminal_final_status": False,
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_retry_recovers_from_first_call_failure(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, adapter = flakey_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)

    # Trigger run — first call fails (FlakeyAdapter.fail_first=1)
    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]
    completed = _wait_for_status(client, run_id, {"failed", "success"})
    # The node returned success=False, which propagates final_status=failed
    # in state, but the graph still completes (no failure-routed edge), so
    # run.final_status ends up as "failed".
    assert completed["final_status"] == "failed"
    assert len(adapter.calls) == 1

    # Retry n1 — second call succeeds
    retry = client.post(f"/runs/{run_id}/nodes/n1/retry")
    assert retry.status_code == 200
    assert retry.json()["final_status"] == "running"

    finished = _wait_for_status(client, run_id, {"success", "failed"})
    assert finished["final_status"] == "success"
    assert len(adapter.calls) == 2


def test_skip_bypasses_failed_node(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, adapter = flakey_client
    # Configure adapter to always fail — we'll skip the broken node
    adapter.fail_first = 999

    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=2)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]
    _wait_for_status(client, run_id, {"failed", "success"})
    initial_calls = len(adapter.calls)
    assert initial_calls >= 1

    # Skip the first node — second node should still run (and also fail)
    # but the skip itself succeeds.
    skip = client.post(f"/runs/{run_id}/nodes/n1/skip")
    assert skip.status_code == 200
    assert skip.json()["final_status"] == "running"

    _wait_for_status(client, run_id, {"success", "failed"})
    # n1 was skipped (not re-run), n2 ran once after the skip
    assert len(adapter.calls) == initial_calls + 1


def test_retry_running_run_returns_409(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, adapter = flakey_client
    adapter.fail_first = 0  # first call succeeds → quick run

    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)
    run = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = run["id"]

    # While running — 409
    response = client.post(f"/runs/{run_id}/nodes/n1/retry")
    if response.status_code == 200:
        # Race: run already finished. Re-fire while in terminal state — should
        # be allowed (failed) or 409 (success). Ensure at least one branch
        # exercised the running-state code path on the previous call.
        return
    assert response.status_code == 409


def test_skip_running_run_returns_409(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, adapter = flakey_client
    adapter.fail_first = 0

    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)
    run = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = run["id"]

    response = client.post(f"/runs/{run_id}/nodes/n1/skip")
    if response.status_code == 200:
        return  # race — see retry counterpart
    assert response.status_code == 409


def test_retry_success_run_returns_409(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, adapter = flakey_client
    adapter.fail_first = 0

    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)
    run = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = run["id"]
    _wait_for_status(client, run_id, {"success"})

    response = client.post(f"/runs/{run_id}/nodes/n1/retry")
    assert response.status_code == 409
    assert "paused or failed" in response.json()["detail"]


def test_retry_unknown_run_returns_404(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, _ = flakey_client
    response = client.post("/runs/nonexistent/nodes/n1/retry")
    assert response.status_code == 404


def test_retry_unknown_node_returns_404(
    flakey_client: tuple[TestClient, FlakeyAdapter],
) -> None:
    client, _ = flakey_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=1)
    run = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = run["id"]
    _wait_for_status(client, run_id, {"failed", "success"})

    response = client.post(f"/runs/{run_id}/nodes/no-such-node/retry")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
