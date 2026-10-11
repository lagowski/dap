"""Per-node ``retry`` routing with feedback injection (#932).

Drives the real engine (stub adapters, as in ``test_runs_trigger.py``)
through a ``coder → tester`` loop where the tester carries a ``retry``
block (#931). A failed tester attempt writes its reason into
``extensions[feedback_into]`` and goes back to ``retry_to``; once
``max_retries`` is spent the run goes to ``on_failure``.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution.runner import DEFAULT_RECURSION_LIMIT
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
POLL_TIMEOUT_S = 10.0

# The coder echoes the feedback it was given so the test can read it back
# from the rendered prompt.
CODER_TEMPLATE = (
    "<agent_prompt><role>coder</role><feedback>"
    "{% if extensions.test_feedback is defined %}{{ extensions.test_feedback }}{% endif %}"
    "</feedback></agent_prompt>"
)
PLAIN_TEMPLATE = "<agent_prompt><role>x</role></agent_prompt>"


class RecordingAdapter:
    """Always succeeds; records every task it receives."""

    kind: RuntimeKind = "api"

    def __init__(self, runtime_id: str) -> None:
        self.id = runtime_id
        self.display_name = runtime_id
        self.tasks: list[RuntimeTask] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.tasks.append(task)
        return RuntimeResult(success=True, output="ok", duration_ms=1)


class ScriptedAdapter(RecordingAdapter):
    """Returns a scripted ``RuntimeResult`` per call; repeats the last one."""

    def __init__(self, runtime_id: str, script: list[RuntimeResult]) -> None:
        super().__init__(runtime_id)
        self.script = script

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.tasks.append(task)
        return self.script[min(len(self.tasks), len(self.script)) - 1]


def _fail(reason: str) -> RuntimeResult:
    return RuntimeResult(success=False, output="", duration_ms=1, errors=[reason])


def _pass(**structured: Any) -> RuntimeResult:
    return RuntimeResult(success=True, output="ok", duration_ms=1, structured=structured or None)


class Harness:
    def __init__(self, client: TestClient, registry: RuntimeRegistry) -> None:
        self.client = client
        self.registry = registry
        self.coder = RecordingAdapter("coder-stub")
        self.escalate = RecordingAdapter("escalate-stub")
        self.mid = RecordingAdapter("mid-stub")
        registry.register(self.coder)
        registry.register(self.mid)
        registry.register(self.escalate)

    def tester(self, script: list[RuntimeResult]) -> ScriptedAdapter:
        adapter = ScriptedAdapter("tester-stub", script)
        self.registry.register(adapter)
        return adapter


@pytest.fixture
def harness() -> Iterator[Harness]:
    tmp = tempfile.mkdtemp(prefix="dap-retry-routing-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    app = create_app(config)
    with authed_test_client(app) as c:
        yield Harness(c, app.state.runtime_registry)


def _agent(client: TestClient, name: str, runtime_id: str, template: str) -> str:
    response = client.post(
        "/agents",
        json={
            "name": name,
            "role": "task_selector",
            "runtime_id": runtime_id,
            "prompt_template": template,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _pipeline(client: TestClient, retry: dict[str, Any] | None, *, mid: bool = False) -> str:
    """coder → [mid →] tester → __end__; escalate → __end__ is only reachable via on_failure."""
    coder = _agent(client, "Coder", "coder-stub", CODER_TEMPLATE)
    tester = _agent(client, "Tester", "tester-stub", PLAIN_TEMPLATE)
    escalate = _agent(client, "Escalate", "escalate-stub", PLAIN_TEMPLATE)
    tester_node: dict[str, Any] = {"id": "tester", "agent_id": tester}
    if retry is not None:
        tester_node["retry"] = retry
    nodes: list[dict[str, Any]] = [{"id": "coder", "agent_id": coder}, tester_node]
    edges: list[dict[str, Any]] = [{"id": "e2", "source": "tester", "target": "__end__"}]
    if mid:
        nodes.append({"id": "mid", "agent_id": _agent(client, "Mid", "mid-stub", PLAIN_TEMPLATE)})
        edges.append({"id": "e1", "source": "coder", "target": "mid"})
        edges.append({"id": "e4", "source": "mid", "target": "tester"})
    else:
        edges.append({"id": "e1", "source": "coder", "target": "tester"})
    if retry is not None and retry["on_failure"] == "escalate":
        nodes.append({"id": "escalate", "agent_id": escalate})
        edges.append({"id": "e3", "source": "escalate", "target": "__end__"})
    response = client.post(
        "/pipelines",
        json={
            "name": "Retry routing",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "coder",
            "nodes": nodes,
            "edges": edges,
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
                # Stub adapters never set a terminal status; keep the
                # "no node failed = success" semantics (#628).
                "requires_terminal_final_status": False,
            },
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _retry(**overrides: Any) -> dict[str, Any]:
    retry: dict[str, Any] = {
        "max_retries": 3,
        "retry_to": "coder",
        "on_failure": "escalate",
        "feedback_into": "test_feedback",
    }
    retry.update(overrides)
    return retry


def _trigger(client: TestClient, pipeline_id: str) -> str:
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "demo", "branch": "main"}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _wait(client: TestClient, run_id: str, statuses: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(f"/runs/{run_id}").json()
        if body["final_status"] in statuses:
            return body
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not reach {statuses} within {POLL_TIMEOUT_S}s")


def _last_state(client: TestClient, run_id: str) -> dict[str, Any]:
    history = client.get(f"/runs/{run_id}/state/history").json()
    assert history, "no state snapshots recorded"
    state: dict[str, Any] = history[-1]["state"]
    return state


def _feedback_seen(coder: RecordingAdapter) -> list[str]:
    out: list[str] = []
    for task in coder.tasks:
        xml = task.prompt_xml
        out.append(xml.split("<feedback>", 1)[1].split("</feedback>", 1)[0])
    return out


def test_tester_fails_twice_then_passes(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester([_fail("2 tests failed"), _fail("1 test failed"), _pass()])
    pipeline_id = _pipeline(client, _retry())

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    assert run["final_status"] == "success", run
    assert len(harness.coder.tasks) == 3
    assert len(tester.tasks) == 3
    assert harness.escalate.tasks == []
    feedback = _feedback_seen(harness.coder)
    assert feedback[0] == ""
    assert "2 tests failed" in feedback[1]
    assert "1 test failed" in feedback[2]
    # A passing attempt resets the node's counter.
    assert _last_state(client, run["id"])["node_attempts"].get("tester", 0) == 0


def test_tester_always_fails_routes_to_on_failure(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester([_fail("still broken")])
    pipeline_id = _pipeline(client, _retry(max_retries=3))

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    # First attempt + 3 retries, then escalate — no infinite loop.
    assert len(tester.tasks) == 4
    assert len(harness.coder.tasks) == 4
    assert len(harness.escalate.tasks) == 1
    # coder/tester x4 + escalate = 9 steps, well under the recursion limit.
    assert len(tester.tasks) * 2 + 1 < DEFAULT_RECURSION_LIMIT
    # Spent retries leave the failure in place.
    assert run["final_status"] == "failed", run
    state = _last_state(client, run["id"])
    assert state["final_status"] == "failed"
    assert "still broken" in (state["verification_reason"] or "")


def test_long_loop_with_max_retries_still_reaches_on_failure(harness: Harness) -> None:
    # coder → mid → tester with 20 retries is 63 loop steps, past the default
    # recursion limit: the runner must size the limit to the retry budget.
    client = harness.client
    tester = harness.tester([_fail("still broken")])
    pipeline_id = _pipeline(client, _retry(max_retries=20), mid=True)
    assert DEFAULT_RECURSION_LIMIT < 3 * 21

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    assert len(tester.tasks) == 21
    assert len(harness.mid.tasks) == 21
    assert len(harness.coder.tasks) == 21
    assert len(harness.escalate.tasks) == 1
    assert run["final_status"] == "failed", run
    state = _last_state(client, run["id"])
    assert "still broken" in (state["verification_reason"] or "")
    assert "GraphRecursionError" not in (state["verification_reason"] or "")


def test_retries_spent_with_end_on_failure(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester([_fail("nope")])
    pipeline_id = _pipeline(client, _retry(max_retries=1, on_failure="__end__"))

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    assert run["final_status"] == "failed"
    assert len(tester.tasks) == 2
    assert harness.escalate.tasks == []


def test_short_circuit_on_alone_triggers_retry(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester(
        [
            _pass(tests_passed=False, last_test_output="AssertionError in test_x"),
            _pass(tests_passed=True, last_test_output="all green"),
        ],
    )
    pipeline_id = _pipeline(
        client,
        _retry(
            short_circuit_on={
                "type": "comparison",
                "field": "tests_passed",
                "operator": "==",
                "value": False,
            },
        ),
    )

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    assert run["final_status"] == "success", run
    assert len(tester.tasks) == 2
    assert len(harness.coder.tasks) == 2
    assert harness.escalate.tasks == []
    feedback = _feedback_seen(harness.coder)
    assert feedback[1], "short-circuited attempt should still inject feedback"


def test_pipeline_without_retry_routes_as_before(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester([_fail("boom")])
    pipeline_id = _pipeline(client, None)

    run = _wait(client, _trigger(client, pipeline_id), {"success", "failed"})

    # No retry block: a failed tester ends the run, no loop back.
    assert run["final_status"] == "failed"
    assert len(harness.coder.tasks) == 1
    assert len(tester.tasks) == 1
    assert _last_state(client, run["id"])["node_attempts"] == {}


def test_manual_retry_rewind_on_pipeline_with_retry_block(harness: Harness) -> None:
    client = harness.client
    # 2 automatic attempts fail (max_retries=1) → escalate → run failed.
    # The manual retry of the tester then passes.
    tester = harness.tester([_fail("a"), _fail("b"), _pass()])
    pipeline_id = _pipeline(client, _retry(max_retries=1))
    run_id = _trigger(client, pipeline_id)
    assert _wait(client, run_id, {"success", "failed"})["final_status"] == "failed"
    assert len(tester.tasks) == 2
    assert len(harness.escalate.tasks) == 1

    response = client.post(f"/runs/{run_id}/nodes/tester/retry")
    assert response.status_code == 200, response.text

    finished = _wait(client, run_id, {"success", "failed"})
    assert len(tester.tasks) == 3
    # The rewound tester passed, so it followed its normal edge to __end__.
    assert len(harness.escalate.tasks) == 1
    assert finished["final_status"] == "success", finished


def test_manual_skip_rewind_on_pipeline_with_retry_block(harness: Harness) -> None:
    client = harness.client
    tester = harness.tester([_fail("a")])
    pipeline_id = _pipeline(client, _retry(max_retries=1))
    run_id = _trigger(client, pipeline_id)
    _wait(client, run_id, {"success", "failed"})
    calls_before = len(tester.tasks)

    response = client.post(f"/runs/{run_id}/nodes/tester/skip")
    assert response.status_code == 200, response.text

    _wait(client, run_id, {"success", "failed"})
    # Skipped, not re-run.
    assert len(tester.tasks) == calls_before
