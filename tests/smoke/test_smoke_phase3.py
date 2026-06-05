"""End-to-end smoke tests for the Phase 3 pipeline bundle.

5-node pipeline: tester → pr-creator → reviewer → gate-phase3 → pr-merger

Tests exercise:
- DAG-order execution
- tester reports test_passed in extensions
- pr-creator writes pr_url into extensions
- gate-phase3 interrupt-before / approve flow
- reviewer APPROVE path triggers merge with rlagowski merge_actor
- reviewer REJECT path blocks merge but run still succeeds
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

from .conftest import replace_adapter, wait_for_status

# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------


class Phase3PythonFuncStub:
    """Stub for python-func nodes (tester, pr-creator, gate-phase3).

    Returns a state_delta with test_passed, pr_url, and __audit metadata.
    All python-func nodes in the Phase 3 pipeline share this adapter.
    """

    id = "python-func"
    display_name = "Python Func Stub (Phase 3)"
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
            duration_ms=50,
            structured={
                "state_delta": {
                    "test_passed": True,
                    "pr_url": "https://github.com/test/repo/pull/99",
                },
                "audit": {"tests_run": 42, "test_passed": True},
            },
        )


class Phase3ReviewerStub:
    """Stub for reviewer node; configurable APPROVE or REJECT decision."""

    id = "reviewer-stub"
    display_name = "Reviewer Stub"
    kind: RuntimeKind = "api"

    def __init__(self, decision: str = "APPROVE") -> None:
        self.decision = decision
        self.call_count = 0

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.call_count += 1
        approved = self.decision == "APPROVE"
        return RuntimeResult(
            success=True,
            output=f"review: {self.decision}",
            duration_ms=200,
            structured={
                "state_delta": {
                    "review_decision": self.decision,
                    "review_approved": approved,
                },
                "audit": {"reviewer": "stub", "verdict": self.decision},
            },
        )


class Phase3PrMergerStub:
    """Stub for pr-merger node.

    Merges only when review_approved is True.  Surfaces merge_actor in audit.
    """

    id = "pr-merger-stub"
    display_name = "PR Merger Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        pipeline_state = task.runtime_config.get("__pipeline_state", {})
        extensions = pipeline_state.get("extensions", {})
        review_approved = extensions.get("review_approved", False)

        return RuntimeResult(
            success=True,
            output=f"merged={review_approved}",
            duration_ms=100,
            structured={
                "state_delta": {
                    "merged": review_approved,
                    "review_approved": review_approved,
                },
                "audit": {"merge_actor": "rlagowski", "merged": review_approved},
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_phase3_pipeline(
    client: TestClient,
    *,
    reviewer_runtime_id: str = "reviewer-stub",
    merger_runtime_id: str = "pr-merger-stub",
    approval_required_nodes: list[str] | None = None,
) -> str:
    """Create the 5-node Phase 3 pipeline.

    tester → pr-creator → reviewer → gate-phase3 → pr-merger → __end__

    gate-phase3 is a passthrough python-func node that acts as a human-approval
    checkpoint.  pr-merger agent definition carries GH_TOKEN_MERGE in
    runtime_config.env to model token separation (rlagowski vs Dixter999).
    """
    if approval_required_nodes is None:
        approval_required_nodes = ["gate-phase3"]

    agents: dict[str, str] = {}

    # Create agents for tester, pr-creator, reviewer, gate-phase3
    for name, runtime_id in [
        ("tester", "python-func"),
        ("pr-creator", "python-func"),
        ("reviewer", reviewer_runtime_id),
        ("gate-phase3", "python-func"),
    ]:
        resp = client.post(
            "/agents",
            json={
                "name": f"Phase3 {name}",
                "role": "task_selector",
                "runtime_id": runtime_id,
                "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            },
        )
        assert resp.status_code == 201, resp.text
        agents[name] = str(resp.json()["id"])

    # pr-merger agent carries GH_TOKEN_MERGE in runtime_config.env
    resp = client.post(
        "/agents",
        json={
            "name": "Phase3 pr-merger",
            "role": "task_selector",
            "runtime_id": merger_runtime_id,
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            "runtime_config": {"env": {"GH_TOKEN_MERGE": "ghp_rlagowski_token"}},
        },
    )
    assert resp.status_code == 201, resp.text
    agents["pr-merger"] = str(resp.json()["id"])

    nodes = [
        {"id": "tester", "agent_id": agents["tester"], "position": {"x": 0, "y": 0}},
        {"id": "pr-creator", "agent_id": agents["pr-creator"], "position": {"x": 100, "y": 0}},
        {"id": "reviewer", "agent_id": agents["reviewer"], "position": {"x": 200, "y": 0}},
        {"id": "gate-phase3", "agent_id": agents["gate-phase3"], "position": {"x": 300, "y": 0}},
        {"id": "pr-merger", "agent_id": agents["pr-merger"], "position": {"x": 400, "y": 0}},
    ]

    edges = [
        {"id": "e1", "source": "tester", "target": "pr-creator"},
        {"id": "e2", "source": "pr-creator", "target": "reviewer"},
        {"id": "e3", "source": "reviewer", "target": "gate-phase3"},
        {"id": "e4", "source": "gate-phase3", "target": "pr-merger"},
        {"id": "e5", "source": "pr-merger", "target": "__end__"},
    ]

    resp = client.post(
        "/pipelines",
        json={
            "name": "Phase 3 Pipeline",
            "description": "5-node cortex phase 3",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "tester",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 10.0,
                "approval_required_nodes": approval_required_nodes,
                # This phase-3 smoke pipeline verifies DAG ordering, gate
                # pause/resume and reviewer routing, not terminal-status
                # enforcement (#628). Opt out so a residual ``running`` keeps the
                # legacy "no node raised = success" semantics these tests rely on.
                "requires_terminal_final_status": False,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# Phase 2 initial_state that Phase 3 consumes as input
_PHASE2_OUTPUT_STATE = {
    "repo": "test/repo",
    "branch": "cortex/issue-174/coder",
    "extensions": {
        "issue_url": "https://github.com/test/repo/issues/174",
        "issue_number": 174,
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def phase3_approve_client() -> Iterator[
    tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub]
]:
    """Client with reviewer configured to return APPROVE (happy path)."""
    tmp = tempfile.mkdtemp(prefix="dap-phase3-approve-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    app = create_app(config)
    pf_stub = Phase3PythonFuncStub()
    reviewer = Phase3ReviewerStub(decision="APPROVE")
    merger = Phase3PrMergerStub()
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        replace_adapter(registry, pf_stub)
        registry.register(reviewer)
        registry.register(merger)
        yield c, pf_stub, reviewer


@pytest.fixture
def phase3_reject_client() -> Iterator[tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub]]:
    """Client with reviewer configured to return REJECT."""
    tmp = tempfile.mkdtemp(prefix="dap-phase3-reject-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    app = create_app(config)
    pf_stub = Phase3PythonFuncStub()
    reviewer = Phase3ReviewerStub(decision="REJECT")
    merger = Phase3PrMergerStub()
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        replace_adapter(registry, pf_stub)
        registry.register(reviewer)
        registry.register(merger)
        yield c, pf_stub, reviewer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase3_all_nodes_execute_in_dag_order(
    phase3_approve_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """Full pipeline runs to success; state history node order matches the 5-node DAG."""
    client, _, _ = phase3_approve_client
    # No gate interruption for this test — ordering only
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]
    assert triggered["final_status"] == "running"

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    expected = ["tester", "pr-creator", "reviewer", "gate-phase3", "pr-merger"]
    assert node_order == expected


def test_phase3_tester_reports_pass(
    phase3_approve_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """Tester node sets test_passed=True in extensions after run completes."""
    client, _, _ = phase3_approve_client
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    state = client.get(f"/runs/{run_id}/state").json()
    assert state["extensions"]["test_passed"] is True


def test_phase3_pr_creator_writes_pr_url(
    phase3_approve_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """pr-creator node writes pr_url into extensions after run completes."""
    client, _, _ = phase3_approve_client
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    state = client.get(f"/runs/{run_id}/state").json()
    pr_url = state["extensions"].get("pr_url", "")
    assert pr_url == "https://github.com/test/repo/pull/99"


def test_phase3_gate_pauses_and_resumes(
    phase3_approve_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """gate-phase3 in approval_required_nodes causes run to pause; approve resumes to success."""
    client, _, _ = phase3_approve_client
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=["gate-phase3"])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]

    # Run pauses at gate-phase3
    paused = wait_for_status(client, run_id, {"paused"})
    assert paused["final_status"] == "paused"

    # Approve the gate
    approve_resp = client.post(f"/runs/{run_id}/nodes/gate-phase3/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["final_status"] == "running"

    # Run completes after approval
    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # gate-phase3 executed after approval
    history = client.get(f"/runs/{run_id}/state/history").json()
    node_order = [snap["node_id"] for snap in history]
    assert "gate-phase3" in node_order


def test_phase3_reviewer_approve_triggers_merge(
    phase3_approve_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """Reviewer APPROVE → pr-merger sets merged=True; audit shows merge_actor='rlagowski'."""
    client, _, reviewer = phase3_approve_client
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # Reviewer returned APPROVE
    assert reviewer.call_count == 1

    # pr-merger set merged=True in extensions
    state = client.get(f"/runs/{run_id}/state").json()
    assert state["extensions"]["merged"] is True

    # pr-merger audit shows rlagowski as merge_actor
    merger_log = client.get(f"/runs/{run_id}/nodes/pr-merger")
    assert merger_log.status_code == 200
    log = merger_log.json()
    assert log["extra_data"]["merge_actor"] == "rlagowski"
    assert log["extra_data"]["merged"] is True


def test_phase3_reviewer_reject_blocks_merge(
    phase3_reject_client: tuple[TestClient, Phase3PythonFuncStub, Phase3ReviewerStub],
) -> None:
    """Reviewer REJECT → pr-merger sets merged=False; run still completes with success."""
    client, _, reviewer = phase3_reject_client
    pipeline_id = _create_phase3_pipeline(client, approval_required_nodes=[])

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": _PHASE2_OUTPUT_STATE},
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    # Merge refusal is not a pipeline failure
    assert completed["final_status"] == "success"

    # Reviewer returned REJECT
    assert reviewer.call_count == 1

    # pr-merger set merged=False (merge blocked)
    state = client.get(f"/runs/{run_id}/state").json()
    assert state["extensions"]["merged"] is False
    assert state["extensions"]["review_approved"] is False
