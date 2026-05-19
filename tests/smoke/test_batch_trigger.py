"""Tests for POST /runs/batch — sequential batch pipeline execution (#473).

Stubs runtime adapter so no real LLM calls happen. The batch endpoint
returns immediately with a BatchRun in 'running' state; we poll GET
/runs/batch/{id} until status reaches a terminal value.
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
POLL_TIMEOUT_S = 10.0

TERMINAL = {"success", "failed", "aborted"}


def _wait_for_batch(client: TestClient, batch_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        response = client.get(f"/runs/batch/{batch_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"BatchRun {batch_id} did not complete within {POLL_TIMEOUT_S}s")


def _issue_number_from_task(task: RuntimeTask) -> int | None:
    """Extract issue_number from task.runtime_config.__pipeline_state.extensions."""
    state: dict[str, Any] = task.runtime_config.get("__pipeline_state", {})
    return state.get("extensions", {}).get("issue_number")


class _StubAdapter:
    id = "batch-stub"
    display_name = "Batch Stub"
    kind: RuntimeKind = "api"
    fail_on_issue: int | None = None  # set to make that issue fail
    received_states: list[dict[str, Any]]

    def __init__(self) -> None:
        self.received_states = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        state: dict[str, Any] = task.runtime_config.get("__pipeline_state", {})
        self.received_states.append(state)
        issue_num = _issue_number_from_task(task)
        if self.fail_on_issue is not None and issue_num == self.fail_on_issue:
            return RuntimeResult(success=False, output="forced failure", duration_ms=1)
        return RuntimeResult(success=True, output=f"done issue {issue_num}", duration_ms=1)


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, _StubAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-batch-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="batch-smoke-secret",
    )
    app = create_app(config)
    stub = _StubAdapter()
    with authed_test_client(app) as client:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(stub)
        yield client, stub


def _create_pipeline(client: TestClient) -> str:
    agent_resp = client.post(
        "/agents",
        json={
            "name": "Batch Agent",
            "role": "task_selector",
            "runtime_id": "batch-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent_id = agent_resp.json()["id"]

    pipe_resp = client.post(
        "/pipelines",
        json={
            "name": "Batch Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [
                {"id": "e1", "source": "__start__", "target": "n1"},
                {"id": "e2", "source": "n1", "target": "__end__"},
            ],
            "defaults": {"max_attempts": 3, "budget_limit_usd": 5.0, "approval_required_nodes": []},
        },
    )
    assert pipe_resp.status_code == 201, pipe_resp.text
    return str(pipe_resp.json()["id"])


# ---------------------------------------------------------------------------
# 1. POST /runs/batch returns 201 with batch_run_id
# ---------------------------------------------------------------------------


def test_batch_trigger_returns_201(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    pipeline_id = _create_pipeline(client)

    resp = client.post(
        "/runs/batch",
        json={
            "pipeline_id": pipeline_id,
            "issue_numbers": [10, 11],
            "stop_on_failure": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["pipeline_id"] == pipeline_id
    assert body["issue_numbers"] == [10, 11]
    assert body["status"] in {"running", "success"}  # may complete instantly


# ---------------------------------------------------------------------------
# 2. All issues execute and produce run entries
# ---------------------------------------------------------------------------


def test_batch_runs_all_issues(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    pipeline_id = _create_pipeline(client)

    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": [1, 2, 3]},
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    batch = _wait_for_batch(client, batch_id)
    assert batch["status"] == "success"
    assert len(batch["results"]) == 3
    assert all(r["status"] == "success" for r in batch["results"])
    # Each result carries the issue_number it targeted
    assert [r["issue_number"] for r in batch["results"]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. stop_on_failure=True halts on first failed run
# ---------------------------------------------------------------------------


def test_stop_on_failure_halts_batch(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, stub = ctx
    stub.fail_on_issue = 2  # issue #2 fails
    pipeline_id = _create_pipeline(client)

    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": [1, 2, 3], "stop_on_failure": True},
    )
    assert resp.status_code == 201, resp.text
    batch = _wait_for_batch(client, resp.json()["id"])

    assert batch["status"] == "failed"
    # Only 2 results: issue 1 (success) and issue 2 (failed) — issue 3 skipped
    assert len(batch["results"]) == 2
    assert batch["results"][0]["status"] == "success"
    assert batch["results"][1]["status"] == "failed"


# ---------------------------------------------------------------------------
# 4. stop_on_failure=False continues after failure
# ---------------------------------------------------------------------------


def test_continue_on_failure(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, stub = ctx
    stub.fail_on_issue = 2
    pipeline_id = _create_pipeline(client)

    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": [1, 2, 3], "stop_on_failure": False},
    )
    assert resp.status_code == 201, resp.text
    batch = _wait_for_batch(client, resp.json()["id"])

    # Batch itself is failed (had a failure) but all 3 ran
    assert batch["status"] == "failed"
    assert len(batch["results"]) == 3
    statuses = [r["status"] for r in batch["results"]]
    assert statuses == ["success", "failed", "success"]


# ---------------------------------------------------------------------------
# 5. auto_approve passes through to each run's extensions
# ---------------------------------------------------------------------------


def test_auto_approve_propagated(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, stub = ctx
    pipeline_id = _create_pipeline(client)
    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": [5, 6], "auto_approve": True},
    )
    assert resp.status_code == 201, resp.text
    _wait_for_batch(client, resp.json()["id"])

    assert len(stub.received_states) == 2
    extensions = [s.get("extensions", {}) for s in stub.received_states]
    assert all(ext.get("auto_approve") is True for ext in extensions)
    assert [ext.get("issue_number") for ext in extensions] == [5, 6]


# ---------------------------------------------------------------------------
# 6. Individual runs are visible in the normal /runs list
# ---------------------------------------------------------------------------


def test_individual_runs_visible(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    pipeline_id = _create_pipeline(client)

    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": [7, 8]},
    )
    assert resp.status_code == 201, resp.text
    _wait_for_batch(client, resp.json()["id"])

    runs_resp = client.get("/runs")
    assert runs_resp.status_code == 200, runs_resp.text
    body = runs_resp.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    all_run_ids = [r["id"] for r in items]
    # Both batch child runs must appear in the global list
    assert len(all_run_ids) >= 2


# ---------------------------------------------------------------------------
# 7. Unknown pipeline_id returns 404
# ---------------------------------------------------------------------------


def test_batch_unknown_pipeline_404(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": "no-such-pipeline", "issue_numbers": [1]},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 8. Empty issue list is rejected
# ---------------------------------------------------------------------------


def test_batch_empty_issue_list_rejected(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    pipeline_id = _create_pipeline(client)
    resp = client.post(
        "/runs/batch",
        json={"pipeline_id": pipeline_id, "issue_numbers": []},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 9. GET /runs/batch/{id} for unknown batch returns 404
# ---------------------------------------------------------------------------


def test_get_batch_not_found(ctx: tuple[TestClient, _StubAdapter]) -> None:
    client, _ = ctx
    resp = client.get("/runs/batch/nonexistent-batch-id")
    assert resp.status_code == 404, resp.text
