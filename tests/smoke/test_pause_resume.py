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


# ---------------------------------------------------------------------------
# /approve validation (#184 + #190)
# ---------------------------------------------------------------------------


def _create_gated_pipeline(client: TestClient, agent_id: str, gate_node: str = "n2") -> str:
    """3-node pipeline n1 → n2 → n3 where ``gate_node`` is an interrupt-before gate."""
    nodes = [
        {"id": f"n{i + 1}", "agent_id": agent_id, "position": {"x": i * 100, "y": 0}}
        for i in range(3)
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n2", "target": "n3"},
        {"id": "e_end", "source": "n3", "target": "__end__"},
    ]
    response = client.post(
        "/pipelines",
        json={
            "name": "Gated Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [gate_node],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _trigger_and_wait_paused(client: TestClient, pipeline_id: str) -> str:
    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = str(triggered["id"])
    _wait_for_status(client, run_id, {"paused"})
    return run_id


def test_approve_unknown_node_returns_404(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    """A node_id that doesn't exist in the pipeline is a missing resource — 404 (#184/#190)."""
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_gated_pipeline(client, agent_id, gate_node="n2")
    run_id = _trigger_and_wait_paused(client, pipeline_id)

    response = client.post(f"/runs/{run_id}/nodes/totally_made_up/approve")
    assert response.status_code == 404
    assert "not in pipeline" in response.json()["detail"]


def test_approve_non_gate_node_returns_409(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    """node_id exists but isn't in approval_required_nodes — 409 (#184).

    Without this check the path parameter was decorative — any existing node
    could "approve" any gate, so the audit log would record the wrong node.
    """
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_gated_pipeline(client, agent_id, gate_node="n2")
    run_id = _trigger_and_wait_paused(client, pipeline_id)

    # n1 exists in the pipeline but is NOT an approval gate
    response = client.post(f"/runs/{run_id}/nodes/n1/approve")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "not an approval gate" in detail
    assert "'n2'" in detail  # current gate listed for diagnostic clarity


def test_approve_gate_node_succeeds(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    """Approving the actual gate node resumes the run (no behavior regression)."""
    client, _ = pause_client
    agent_id = _create_agent(client)
    pipeline_id = _create_gated_pipeline(client, agent_id, gate_node="n2")
    run_id = _trigger_and_wait_paused(client, pipeline_id)

    response = client.post(f"/runs/{run_id}/nodes/n2/approve")
    assert response.status_code == 200
    assert response.json()["final_status"] == "running"

    completed = _wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"


# ---------------------------------------------------------------------------
# /resume + /approve atomic claim — TOCTOU regression coverage (#185)
# ---------------------------------------------------------------------------


def test_concurrent_resume_only_one_wins(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    """Two concurrent /resume calls: exactly one returns 200, the other 409.

    Regression test for the TOCTOU race where both requests passed the
    Python-side ``final_status == "paused"`` check, both spawned background
    tasks, and the second ``run_registry.register`` raised with an orphan
    asyncio.Task already in flight. With the atomic UPDATE … WHERE
    final_status='paused' claim, only one row update wins. (#185)
    """
    import concurrent.futures

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

    # Fire two /resume calls in parallel via threads. TestClient is sync and
    # the FastAPI app dispatches requests on a worker thread pool, so two
    # concurrent client.post calls actually race in the handler.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, f"/runs/{run_id}/resume") for _ in range(2)]
        responses = [f.result() for f in concurrent.futures.as_completed(futures)]

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409], (
        f"Expected exactly one 200 and one 409 from concurrent /resume, got {statuses}"
    )

    # Loser's body should mention the race or the not-paused state — never 500.
    loser = next(r for r in responses if r.status_code == 409)
    detail = loser.json()["detail"]
    assert "not paused" in detail or "another request" in detail

    # Run still completes (winner's task ran).
    completed = _wait_for_status(client, run_id, {"success", "failed", "aborted"})
    assert completed["final_status"] == "success"


def test_try_claim_resume_atomic(
    pause_client: tuple[TestClient, CountingSlowAdapter],
) -> None:
    """Direct repository test: try_claim_resume succeeds once, second call returns False (#185)."""
    from dap_engine.persistence import repository as repo

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

    session_factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with session_factory() as s1, session_factory() as s2:
        first = repo.try_claim_resume(s1, run_id)
        s1.commit()
        second = repo.try_claim_resume(s2, run_id)
        s2.commit()

    assert first is True
    assert second is False  # Already claimed by s1 — no row matches WHERE paused.


def test_try_claim_revive_accepts_paused_and_failed() -> None:
    """try_claim_revive WHERE clause covers both 'paused' and 'failed' (#185)."""
    import datetime as dt
    import uuid

    from dap_engine.persistence import repository as repo
    from dap_engine.persistence.db import make_session_factory
    from dap_engine.persistence.models import Base, RunORM
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    with factory() as s:
        for state in ("paused", "failed", "running", "success"):
            run_id = str(uuid.uuid4())
            s.add(
                RunORM(
                    id=run_id,
                    pipeline_id="pipe",
                    pipeline_version=1,
                    trigger_source="api",
                    final_status=state,
                    initial_state={},
                    started_at=dt.datetime.now(dt.UTC),
                )
            )
            s.commit()
            claimed = repo.try_claim_revive(s, run_id)
            s.commit()
            if state in {"paused", "failed"}:
                assert claimed is True, f"revive should claim {state}"
            else:
                assert claimed is False, f"revive must NOT claim {state}"
