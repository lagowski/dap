"""End-to-end smoke tests for the Phase 2 pipeline bundle.

6-node pipeline: git-branch → coder → designer → documenter → code-reviewer → gate-phase2

Tests exercise:
- DAG-order execution
- code-reviewer conditional routing (clean → gate, needs_work → retry from coder)
- Retry loop capped at 2 attempts
- gate-phase2 interrupt-before / approve flow
- timeout_ms=null nodes run without engine error
- extra_data populated per node via __audit
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from .conftest import replace_adapter, wait_for_status


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------


class Phase2BashStub:
    """Stub for the git-branch node (runtime_id: bash)."""

    id = "bash"
    display_name = "Bash Stub"
    kind: RuntimeKind = "cli"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(
            success=True,
            output="switched to branch feature-123",
            duration_ms=50,
            structured={
                "state_delta": {"branch": "feature-123"},
                "audit": {"operation": "git-checkout"},
            },
        )


class Phase2PythonFuncStub:
    """Stub for python-func nodes (coder, designer, documenter).

    Returns a state_delta with extensions and __audit metadata.
    """

    id = "python-func"
    display_name = "Python Func Stub"
    kind: RuntimeKind = "api"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.calls.append(task.execution_id)
        return RuntimeResult(
            success=True,
            output="ok",
            duration_ms=100,
            structured={
                "state_delta": {
                    "modified_files": ["src/main.py"],
                    "implementation_notes": "stub implementation",
                },
                "audit": {"agent": "stub", "tokens_used": 42},
            },
        )


class Phase2CodeReviewerStub:
    """Stub for code-reviewer that can be configured to return clean or needs_work.

    Uses a separate runtime_id so we can control its behavior independently.
    """

    id = "code-reviewer-stub"
    display_name = "Code Reviewer Stub"
    kind: RuntimeKind = "api"

    def __init__(self, review_results: list[str] | None = None) -> None:
        # Each call pops from the front. Default: always clean.
        self._results = list(review_results or ["clean"])
        self.call_count = 0

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.call_count += 1
        # Pop next result, default to "clean" if exhausted
        if self._results:
            status = self._results.pop(0)
        else:
            status = "clean"

        # Read current review_attempts from pipeline state
        pipeline_state = task.runtime_config.get("__pipeline_state", {})
        extensions = pipeline_state.get("extensions", {})
        current_attempts = extensions.get("review_attempts", 0)

        return RuntimeResult(
            success=True,
            output=f"review: {status}",
            duration_ms=200,
            structured={
                "state_delta": {
                    "review_status": status,
                    "review_attempts": current_attempts + 1,
                },
                "audit": {"reviewer": "stub", "verdict": status},
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_phase2_pipeline(
    client: TestClient,
    *,
    reviewer_runtime_id: str = "code-reviewer-stub",
    approval_required_nodes: list[str] | None = None,
) -> str:
    """Create the 6-node Phase 2 pipeline with conditional edges.

    git-branch → coder → designer → documenter → code-reviewer → gate-phase2

    code-reviewer has conditional routing:
    - extensions.review_status == "clean" → gate-phase2
    - extensions.review_attempts < 2 AND extensions.review_status == "needs_work" → coder (retry)
    - fallback (review_attempts >= 2) → gate-phase2
    """
    if approval_required_nodes is None:
        approval_required_nodes = ["gate-phase2"]

    # Create agents for each node
    agents: dict[str, str] = {}
    for name, runtime_id in [
        ("git-branch", "bash"),
        ("coder", "python-func"),
        ("designer", "python-func"),
        ("documenter", "python-func"),
        ("code-reviewer", reviewer_runtime_id),
        ("gate-phase2", "python-func"),
    ]:
        resp = client.post(
            "/agents",
            json={
                "name": f"Phase2 {name}",
                "role": "task_selector",
                "runtime_id": runtime_id,
                "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            },
        )
        assert resp.status_code == 201, resp.text
        agents[name] = str(resp.json()["id"])

    nodes = [
        {"id": "git-branch", "agent_id": agents["git-branch"], "position": {"x": 0, "y": 0}},
        {"id": "coder", "agent_id": agents["coder"], "position": {"x": 100, "y": 0}},
        {"id": "designer", "agent_id": agents["designer"], "position": {"x": 200, "y": 0}},
        {"id": "documenter", "agent_id": agents["documenter"], "position": {"x": 300, "y": 0}},
        {
            "id": "code-reviewer",
            "agent_id": agents["code-reviewer"],
            "position": {"x": 400, "y": 0},
        },
        {"id": "gate-phase2", "agent_id": agents["gate-phase2"], "position": {"x": 500, "y": 0}},
    ]

    edges = [
        # Linear flow: git-branch → coder → designer → documenter → code-reviewer
        {"id": "e1", "source": "git-branch", "target": "coder"},
        {"id": "e2", "source": "coder", "target": "designer"},
        {"id": "e3", "source": "designer", "target": "documenter"},
        {"id": "e4", "source": "documenter", "target": "code-reviewer"},
        # Conditional edges from code-reviewer:
        # 1. clean → gate-phase2
        {
            "id": "e5-clean",
            "source": "code-reviewer",
            "target": "gate-phase2",
            "condition": {
                "type": "comparison",
                "field": "extensions.review_status",
                "operator": "==",
                "value": "clean",
            },
        },
        # 2. needs_work + attempts < 2 → coder (retry)
        {
            "id": "e5-retry",
            "source": "code-reviewer",
            "target": "coder",
            "condition": {
                "type": "and",
                "children": [
                    {
                        "type": "comparison",
                        "field": "extensions.review_status",
                        "operator": "==",
                        "value": "needs_work",
                    },
                    {
                        "type": "comparison",
                        "field": "extensions.review_attempts",
                        "operator": "<",
                        "value": 2,
                    },
                ],
            },
        },
        # 3. fallback (no condition) → gate-phase2 (review_attempts >= 2)
        {"id": "e5-fallback", "source": "code-reviewer", "target": "gate-phase2"},
        # gate-phase2 → __end__
        {"id": "e6", "source": "gate-phase2", "target": "__end__"},
    ]

    resp = client.post(
        "/pipelines",
        json={
            "name": "Phase 2 Pipeline",
            "description": "6-node cortex phase 2",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "git-branch",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 10.0,
                "approval_required_nodes": approval_required_nodes,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def phase2_clean_client() -> Iterator[tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub]]:
    """Client with code-reviewer configured to return 'clean' on first call."""
    tmp = tempfile.mkdtemp(prefix="dap-phase2-clean-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    pf_stub = Phase2PythonFuncStub()
    reviewer = Phase2CodeReviewerStub(review_results=["clean"])
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        replace_adapter(registry, Phase2BashStub())
        replace_adapter(registry, pf_stub)
        registry.register(reviewer)
        yield c, pf_stub, reviewer


@pytest.fixture
def phase2_retry_client() -> Iterator[tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub]]:
    """Client with code-reviewer configured to return 'needs_work' repeatedly."""
    tmp = tempfile.mkdtemp(prefix="dap-phase2-retry-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    pf_stub = Phase2PythonFuncStub()
    # needs_work, needs_work, then clean (but cap should trigger at 2)
    reviewer = Phase2CodeReviewerStub(review_results=["needs_work", "needs_work", "clean"])
    with TestClient(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        replace_adapter(registry, Phase2BashStub())
        replace_adapter(registry, pf_stub)
        registry.register(reviewer)
        yield c, pf_stub, reviewer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase2_all_nodes_execute_in_dag_order(
    phase2_clean_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """Full pipeline clean-path run executes nodes in correct DAG order."""
    client, _, _ = phase2_clean_client
    # No gate interruption for this test — test ordering only
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]
    assert triggered["final_status"] == "running"

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # Verify execution order via state history snapshots
    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    expected = ["git-branch", "coder", "designer", "documenter", "code-reviewer", "gate-phase2"]
    assert node_order == expected


def test_phase2_code_reviewer_routes_clean_to_gate(
    phase2_clean_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """When code-reviewer returns review_status='clean', next node is gate-phase2."""
    client, _, reviewer = phase2_clean_client
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # Reviewer called exactly once (clean path, no retry)
    assert reviewer.call_count == 1

    # Verify code-reviewer → gate-phase2 (no coder retry)
    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    cr_idx = node_order.index("code-reviewer")
    assert node_order[cr_idx + 1] == "gate-phase2"


def test_phase2_retry_loop_capped_at_2_attempts(
    phase2_retry_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """needs_work routing retries from coder at most once (attempts < 2), then falls through."""
    client, pf_stub, reviewer = phase2_retry_client
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # code-reviewer called twice: first needs_work (attempts=1 < 2 → retry),
    # second needs_work (attempts=2 >= 2 → fallback to gate-phase2)
    assert reviewer.call_count == 2

    # Verify the retry loop happened: coder appears more than once
    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    coder_count = node_order.count("coder")
    assert coder_count == 2, f"Expected coder to run twice (initial + 1 retry), got {coder_count}"

    # Pipeline terminates at gate-phase2
    assert node_order[-1] == "gate-phase2"


def test_phase2_gate_pauses_via_interrupt_before(
    phase2_clean_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """gate-phase2 in approval_required_nodes causes run to pause before executing it."""
    client, _, _ = phase2_clean_client
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=["gate-phase2"])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]

    # Run should pause at gate-phase2
    paused = wait_for_status(client, run_id, {"paused"})
    assert paused["final_status"] == "paused"

    # Approve the gate
    approve_resp = client.post(f"/runs/{run_id}/nodes/gate-phase2/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["final_status"] == "running"

    # Run completes after approval
    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # gate-phase2 executed after approval
    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    assert "gate-phase2" in node_order


def test_phase2_timeout_ms_null_nodes_complete(
    phase2_clean_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """Nodes with timeout_ms=null (no timeout) execute without engine timeout error."""
    client, pf_stub, _ = phase2_clean_client
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # python-func stub was called (coder, designer, documenter, gate-phase2 = 4 calls min)
    assert len(pf_stub.calls) >= 4


def test_phase2_extra_data_populated_per_node(
    phase2_clean_client: tuple[TestClient, Phase2PythonFuncStub, Phase2CodeReviewerStub],
) -> None:
    """After run completes, node execution logs have non-empty extra_data."""
    client, _, _ = phase2_clean_client
    pipeline_id = _create_phase2_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "test/repo", "branch": "main"}},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # Check extra_data for each node
    for node_id in ["git-branch", "coder", "designer", "documenter", "code-reviewer"]:
        log_resp = client.get(f"/runs/{run_id}/nodes/{node_id}")
        assert log_resp.status_code == 200, f"No log for {node_id}: {log_resp.text}"
        log = log_resp.json()
        assert log["extra_data"] is not None, f"extra_data is None for node {node_id}"
        assert len(log["extra_data"]) > 0, f"extra_data is empty for node {node_id}"
