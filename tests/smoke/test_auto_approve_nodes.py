"""Tests for project-level auto_approve_nodes (#477).

When a project's ``auto_approve_nodes`` list is set, the engine auto-resumes
at listed approval gates without waiting for a human POST /approve. Unlisted
gates still pause normally.

Also covers per-run override: ``initial_state.extensions.auto_approve_nodes``
takes precedence over the project-level setting.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client
from tests.smoke.conftest import wait_for_status

POLL_TIMEOUT_S = 8.0


class _StubAdapter:
    id = "auto-approve-nodes-stub"
    display_name = "Auto-Approve Nodes Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        await asyncio.sleep(0.01)
        return RuntimeResult(success=True, output="ok", duration_ms=1)


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, _StubAdapter]]:
    tmp = tempfile.mkdtemp(prefix="dap-auto-approve-nodes-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="auto-approve-nodes-secret",
    )
    app = create_app(config)
    stub = _StubAdapter()
    with authed_test_client(app) as client:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(stub)
        yield client, stub


def _create_agent(client: TestClient) -> str:
    resp = client.post(
        "/agents",
        json={
            "name": "AANodes Test",
            "role": "task_selector",
            "runtime_id": "auto-approve-nodes-stub",
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _create_pipeline(
    client: TestClient,
    agent_id: str,
    *,
    num_nodes: int = 3,
    gate_nodes: list[str] | None = None,
) -> str:
    """Create a linear N-node pipeline with the given approval gates."""
    if gate_nodes is None:
        gate_nodes = ["n2"]
    nodes = [
        {"id": f"n{i + 1}", "agent_id": agent_id, "position": {"x": i * 100, "y": 0}}
        for i in range(num_nodes)
    ]
    edges = [
        {"id": f"e{i + 1}", "source": f"n{i + 1}", "target": f"n{i + 2}"}
        for i in range(num_nodes - 1)
    ]
    edges.append({"id": "e_end", "source": f"n{num_nodes}", "target": "__end__"})
    resp = client.post(
        "/pipelines",
        json={
            "name": "AANodes Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": gate_nodes,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _create_project(
    client: TestClient,
    pipeline_id: str,
    *,
    auto_approve_nodes: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "name": "AANodes Project",
        "description": "",
        "pipelines": {"cortex": pipeline_id},
        "env_vars": {},
    }
    if auto_approve_nodes is not None:
        payload["auto_approve_nodes"] = auto_approve_nodes
    resp = client.post("/projects", json=payload)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ---------------------------------------------------------------------------
# 1. Listed gate auto-approves, run completes
# ---------------------------------------------------------------------------


def test_listed_gate_auto_approved_run_completes(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """Project with auto_approve_nodes=[n2]; pipeline n1→n2(gate)→n3 completes."""
    client, _ = ctx
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3, gate_nodes=["n2"])
    project_id = _create_project(client, pipeline_id, auto_approve_nodes=["n2"])

    resp = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "project_id": project_id},
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    final = wait_for_status(client, run_id, {"success", "failed", "aborted"}, timeout_s=POLL_TIMEOUT_S)
    assert final["final_status"] == "success", (
        f"listed gate should be auto-approved; got {final['final_status']}"
    )


# ---------------------------------------------------------------------------
# 2. Unlisted gate still pauses
# ---------------------------------------------------------------------------


def test_unlisted_gate_still_pauses(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """n2 auto-approved, n3 (unlisted) still pauses."""
    client, _ = ctx
    agent_id = _create_agent(client)
    # n1 → gate-n2 → gate-n3 → n4
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=4, gate_nodes=["n2", "n3"])
    project_id = _create_project(client, pipeline_id, auto_approve_nodes=["n2"])

    resp = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "project_id": project_id},
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    # Should pause at n3 (n2 auto-approved, n3 is not in the list)
    final = wait_for_status(client, run_id, {"paused"}, timeout_s=POLL_TIMEOUT_S)
    assert final["final_status"] == "paused"
    assert final["paused_at_node"] == "n3", (
        f"expected pause at n3, got {final['paused_at_node']}"
    )


# ---------------------------------------------------------------------------
# 3. Per-run extensions.auto_approve_nodes overrides project setting
# ---------------------------------------------------------------------------


def test_per_run_extensions_override_project(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """Run-level extensions.auto_approve_nodes=[] overrides project's [n2]; gate pauses."""
    client, _ = ctx
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3, gate_nodes=["n2"])
    project_id = _create_project(client, pipeline_id, auto_approve_nodes=["n2"])

    resp = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "project_id": project_id,
            # Caller explicitly passes empty list to disable auto-approve for this run
            "initial_state": {"extensions": {"auto_approve_nodes": []}},
        },
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    # Should pause at n2 since per-run override disabled auto-approve
    final = wait_for_status(client, run_id, {"paused"}, timeout_s=POLL_TIMEOUT_S)
    assert final["final_status"] == "paused"
    assert final["paused_at_node"] == "n2"


# ---------------------------------------------------------------------------
# 4. Project without auto_approve_nodes pauses normally
# ---------------------------------------------------------------------------


def test_project_without_auto_approve_nodes_pauses_normally(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """Project with no auto_approve_nodes — gate still requires human approval."""
    client, _ = ctx
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3, gate_nodes=["n2"])
    project_id = _create_project(client, pipeline_id)  # auto_approve_nodes omitted

    resp = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "project_id": project_id},
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    final = wait_for_status(client, run_id, {"paused"}, timeout_s=POLL_TIMEOUT_S)
    assert final["final_status"] == "paused"
    assert final["paused_at_node"] == "n2"


# ---------------------------------------------------------------------------
# 5. GET /projects/{id} returns auto_approve_nodes
# ---------------------------------------------------------------------------


def test_project_returns_auto_approve_nodes(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """auto_approve_nodes is persisted and returned on GET /projects/{id}."""
    client, _ = ctx
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_id = _create_project(client, pipeline_id, auto_approve_nodes=["n2", "n4"])

    fetched = client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["auto_approve_nodes"] == ["n2", "n4"]


# ---------------------------------------------------------------------------
# 6. Run-level auto_approve_nodes in initial_state (no project)
# ---------------------------------------------------------------------------


def test_run_level_auto_approve_nodes_without_project(
    ctx: tuple[TestClient, _StubAdapter],
) -> None:
    """extensions.auto_approve_nodes works on ad-hoc runs without a project."""
    client, _ = ctx
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id, num_nodes=3, gate_nodes=["n2"])

    resp = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {"extensions": {"auto_approve_nodes": ["n2"]}},
        },
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    final = wait_for_status(client, run_id, {"success", "failed", "aborted"}, timeout_s=POLL_TIMEOUT_S)
    assert final["final_status"] == "success"
