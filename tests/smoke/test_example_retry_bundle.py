"""The tester → coder retry example bundle really works (#936, closes out #811).

``examples/pipelines/tester-coder-retry.pipeline-bundle.json`` is a coder on a git
branch, a tester with a ``retry`` block and an ``escalate`` node reachable only
through ``on_failure``. These tests import it through the real API and run it
through the real engine. Only the backends are faked: the registered
``claude-code``, ``codex`` and ``bash`` adapters get a scripted ``execute``, so
the routing, the feedback injection, the ``not`` condition on ``diff_stats`` and
the backend fallback chain all run as shipped.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import ERROR_CATEGORY_KEY, OutputCallback, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "pipelines"
    / "tester-coder-retry.pipeline-bundle.json"
)
POLL_TIMEOUT_S = 10.0


class Script:
    """Stands in for one adapter's ``execute``: returns scripted results, repeats the last."""

    def __init__(self, results: list[RuntimeResult]) -> None:
        self.results = results
        self.tasks: list[RuntimeTask] = []

    async def __call__(
        self, task: RuntimeTask, on_output: OutputCallback | None = None
    ) -> RuntimeResult:
        self.tasks.append(task)
        return self.results[min(len(self.tasks), len(self.results)) - 1]


def _committed(files: int = 1) -> RuntimeResult:
    """A coder run that changed ``files`` files, as the CLI runtimes report it (#933)."""
    changed = [f"src/mod{i}.py" for i in range(files)]
    return RuntimeResult(
        success=True,
        output="done",
        duration_ms=1,
        structured={
            "changed_files": changed,
            "commits_since_base": (
                [{"sha": "abc123", "subject": "fix", "author": "bot"}] if files else []
            ),
            "diff_stats": {"additions": 3 * files, "deletions": 0, "files": files},
        },
    )


def _tests(passed: bool, detail: str = "") -> RuntimeResult:
    if passed:
        return RuntimeResult(success=True, output="5 passed", duration_ms=1)
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=1,
        errors=[f"Command exited with code 1: {detail}"],
    )


def _rate_limited() -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=1,
        errors=["429 slow down"],
        structured={ERROR_CATEGORY_KEY: "rate_limit"},
    )


class Harness:
    def __init__(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = client
        self.monkeypatch = monkeypatch
        self.registry: RuntimeRegistry = client.app.state.runtime_registry  # type: ignore[attr-defined]

    def script(self, runtime_id: str, results: list[RuntimeResult]) -> Script:
        script = Script(results)
        self.monkeypatch.setattr(self.registry.get(runtime_id), "execute", script)
        return script

    def bash(self, tester: list[RuntimeResult]) -> tuple[Script, list[RuntimeTask]]:
        """Script the tester's ``pytest -q``; run the shipped escalate command for real."""
        tester_script = Script(tester)
        escalated: list[RuntimeTask] = []
        adapter = self.registry.get("bash")
        real_execute = adapter.execute

        async def execute(
            task: RuntimeTask, on_output: OutputCallback | None = None
        ) -> RuntimeResult:
            if task.runtime_config.get("command") == "pytest -q":
                return await tester_script(task)
            escalated.append(task)
            return await real_execute(task, on_output)

        self.monkeypatch.setattr(adapter, "execute", execute)
        return tester_script, escalated


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    tmp = tempfile.mkdtemp(prefix="dap-retry-bundle-")
    app = create_app(
        EngineConfig(
            db_path=str(Path(tmp) / "state.db"),
            auth_jwt_secret="smoke-secret",
            # The tester and escalate nodes are bash; the smoke user isn't an admin.
            allow_bash_runtime_for_non_admin=True,
        )
    )
    with authed_test_client(app) as c:
        yield Harness(c, monkeypatch)


def _import_and_run(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    imported = client.post("/pipelines/import", json=json.loads(BUNDLE.read_text()))
    assert imported.status_code == 201, imported.text
    started = client.post(
        "/runs",
        json={
            "pipeline_id": imported.json()["id"],
            "initial_state": {"repo": "acme/widgets", "branch": "develop"},
        },
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        run: dict[str, Any] = client.get(f"/runs/{run_id}").json()
        if run["final_status"] != "running":
            state: dict[str, Any] = client.get(f"/runs/{run_id}/state").json()
            return run, state
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {POLL_TIMEOUT_S}s")


def _feedback(task: RuntimeTask) -> str:
    xml = task.prompt_xml
    if "<feedback>" not in xml:
        return ""
    return xml.split("<feedback>", 1)[1].split("</feedback>", 1)[0]


def test_bundle_declares_the_retry_loop_and_the_fallback_chain() -> None:
    bundle = json.loads(BUNDLE.read_text())
    nodes = {n["id"]: n for n in bundle["pipeline"]["nodes"]}

    retry = nodes["tester"]["retry"]
    assert retry["retry_to"] == "coder"
    assert retry["on_failure"] == "escalate"
    assert retry["feedback_into"] == "test_feedback"
    assert retry["short_circuit_on"]["type"] == "not"
    coder_agent = bundle["bundled_agents"][nodes["coder"]["agent_id"]]
    assert coder_agent["runtime_config"]["branch"]
    coder_assignment = bundle["backend_profiles"]["agent_assignments"]["overrides"]["coder"]
    assert coder_assignment == {"profile": "claude", "fallback": ["codex"]}


def test_failed_tests_loop_back_with_feedback_then_pass(harness: Harness) -> None:
    coder = harness.script("claude-code", [_committed()])
    tester, escalate = harness.bash([_tests(False, "assert 1 < 2 & more"), _tests(True)])

    run, state = _import_and_run(harness.client)

    assert run["final_status"] == "success", run
    assert len(coder.tasks) == 2
    assert len(tester.tasks) == 2
    assert escalate == []
    # The first attempt had no feedback; the retry carries the tester's
    # failure, escaped so the prompt stays valid XML.
    assert _feedback(coder.tasks[0]) == ""
    assert "assert 1 &lt; 2 &amp; more" in _feedback(coder.tasks[1])
    # The coder runs on its branch, and its diff landed in state.
    assert coder.tasks[0].runtime_config["branch"] == "dap/retry-example"
    assert state["diff_stats"]["files"] == 1
    assert state["changed_files"] == ["src/mod0.py"]
    assert state["node_attempts"].get("tester", 0) == 0


def test_an_empty_diff_counts_as_a_failed_attempt(harness: Harness) -> None:
    # Green tests on a coder run that changed nothing: ``not diff_stats.files > 0``
    # short-circuits the attempt back to the coder.
    coder = harness.script("claude-code", [_committed(files=0), _committed(files=2)])
    tester, escalate = harness.bash([_tests(True)])

    run, state = _import_and_run(harness.client)

    assert run["final_status"] == "success", run
    assert len(coder.tasks) == 2
    assert len(tester.tasks) == 2
    assert escalate == []
    assert "short_circuit_on" in _feedback(coder.tasks[1])
    assert state["diff_stats"]["files"] == 2


def test_spent_retries_go_to_escalate(harness: Harness) -> None:
    coder = harness.script("claude-code", [_committed()])
    tester, escalate = harness.bash([_tests(False, "still red")])

    run, state = _import_and_run(harness.client)

    # First attempt + max_retries (3), then on_failure.
    assert len(tester.tasks) == 4
    assert len(coder.tasks) == 4
    assert len(escalate) == 1
    assert run["final_status"] == "failed", run
    logs = harness.client.get(f"/runs/{run['id']}/nodes").json()
    tester_errors = [log["error_message"] for log in logs if log["node_id"] == "tester"]
    assert len(tester_errors) == 4
    assert "still red" in (tester_errors[-1] or "")
    assert "after every retry" in (state["verification_reason"] or "")


def test_a_coder_that_never_changes_anything_fails_the_run(harness: Harness) -> None:
    # Short-circuited attempts never set final_status; escalate must fail the
    # run itself, or it would end "success" with nothing done.
    coder = harness.script("claude-code", [_committed(files=0)])
    _, escalate = harness.bash([_tests(True)])

    run, _ = _import_and_run(harness.client)

    assert len(coder.tasks) == 4
    assert len(escalate) == 1
    assert run["final_status"] == "failed", run


def test_rate_limited_coder_falls_back_to_codex(harness: Harness) -> None:
    claude = harness.script("claude-code", [_rate_limited()])
    codex = harness.script("codex", [_committed()])
    tester, escalate = harness.bash([_tests(True)])

    run, _ = _import_and_run(harness.client)

    assert run["final_status"] == "success", run
    assert len(claude.tasks) == 1
    assert len(codex.tasks) == 1
    # The fallback profile keeps the agent's git config.
    assert codex.tasks[0].runtime_config["branch"] == "dap/retry-example"
    logs = harness.client.get(f"/runs/{run['id']}/nodes").json()
    coder_logs = [log for log in logs if log["node_id"] == "coder"]
    assert [log["extra_data"]["backend_attempt"] for log in coder_logs] == [
        {"attempt": 1, "profile_id": "claude", "error_category": "rate_limit"},
        {"attempt": 2, "profile_id": "codex", "error_category": None},
    ]
    assert len(tester.tasks) == 1
    assert escalate == []
