"""Backend profile fallback chain (#935).

Drives the real engine (stub adapters, as in ``test_retry_routing.py``)
through a single ``coder`` node whose backend assignment carries a
``fallback`` chain. A result that failed for a backend reason (#934's
``is_backend_error``) re-runs the node on the next profile; any other
failure ends it on the profile that produced it.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import PipelineVersionORM
from dap_runtimes import RuntimeRegistry
from dap_types import (
    ERROR_CATEGORY_KEY,
    HealthStatus,
    OutputCallback,
    RuntimeKind,
    RuntimeResult,
    RuntimeTask,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.smoke._auth import authed_test_client

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 10.0
TEMPLATE = "<agent_prompt><role>coder</role></agent_prompt>"


class ScriptedAdapter:
    """Returns ``result`` on every call and records the tasks it got."""

    kind: RuntimeKind = "api"

    def __init__(self, runtime_id: str, result: RuntimeResult) -> None:
        self.id = runtime_id
        self.display_name = runtime_id
        self.result = result
        self.tasks: list[RuntimeTask] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.tasks.append(task)
        return self.result


def _ok() -> RuntimeResult:
    return RuntimeResult(success=True, output="ok", duration_ms=1)


def _backend_error(category: str, message: str) -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=1,
        errors=[message],
        structured={ERROR_CATEGORY_KEY: category},
    )


def _plain_failure(message: str) -> RuntimeResult:
    return RuntimeResult(success=False, output="", duration_ms=1, errors=[message])


class Harness:
    def __init__(self, client: TestClient, registry: RuntimeRegistry) -> None:
        self.client = client
        self.registry = registry

    def adapter(self, runtime_id: str, result: RuntimeResult) -> ScriptedAdapter:
        adapter = ScriptedAdapter(runtime_id, result)
        self.registry.register(adapter)
        return adapter


@pytest.fixture
def harness() -> Iterator[Harness]:
    tmp = tempfile.mkdtemp(prefix="dap-backend-fallback-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    app = create_app(config)
    with authed_test_client(app) as c:
        yield Harness(c, app.state.runtime_registry)


def _profiles(override: Any) -> dict[str, Any]:
    return {
        "available": {
            "primary": {"runtime_id": "primary-stub", "runtime_config": {"model": "p"}},
            "secondary": {"runtime_id": "secondary-stub", "runtime_config": {"model": "s"}},
            "tertiary": {"runtime_id": "tertiary-stub", "runtime_config": {"model": "t"}},
        },
        "agent_assignments": {"overrides": {"coder": override}},
    }


def _pipeline_payload(agent_id: str, backend_profiles: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "Backend fallback",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "coder",
        "nodes": [{"id": "coder", "agent_id": agent_id}],
        "edges": [{"id": "e1", "source": "coder", "target": "__end__"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
            "requires_terminal_final_status": False,
        },
        "backend_profiles": backend_profiles,
    }


def _agent(client: TestClient) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Coder",
            "role": "task_selector",
            "runtime_id": "base-stub",
            "prompt_template": TEMPLATE,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _pipeline(client: TestClient, backend_profiles: dict[str, Any]) -> str:
    response = client.post("/pipelines", json=_pipeline_payload(_agent(client), backend_profiles))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _run(client: TestClient, pipeline_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "demo", "branch": "main"}},
    )
    assert response.status_code == 201, response.text
    run_id = str(response.json()["id"])
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(f"/runs/{run_id}").json()
        if body["final_status"] in {"success", "failed"}:
            logs: list[dict[str, Any]] = client.get(f"/runs/{run_id}/nodes").json()
            return body, logs
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not finish within {POLL_TIMEOUT_S}s")


def _attempts(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [log["extra_data"]["backend_attempt"] for log in logs]


def test_rate_limited_primary_falls_back_to_secondary(harness: Harness) -> None:
    primary = harness.adapter("primary-stub", _backend_error("rate_limit", "429 slow down"))
    secondary = harness.adapter("secondary-stub", _ok())
    tertiary = harness.adapter("tertiary-stub", _ok())
    pipeline_id = _pipeline(
        harness.client,
        _profiles({"profile": "primary", "fallback": ["secondary", "tertiary"]}),
    )

    run, logs = _run(harness.client, pipeline_id)

    assert run["final_status"] == "success", run
    assert len(primary.tasks) == 1
    assert len(secondary.tasks) == 1
    assert tertiary.tasks == []
    # The fallback profile is resolved like a primary: its config reaches the adapter.
    assert secondary.tasks[0].runtime_config["model"] == "s"
    assert [log["runtime_id"] for log in logs] == ["primary-stub", "secondary-stub"]
    assert [log["status"] for log in logs] == ["failed", "success"]
    assert _attempts(logs) == [
        {"attempt": 1, "profile_id": "primary", "error_category": "rate_limit"},
        {"attempt": 2, "profile_id": "secondary", "error_category": None},
    ]
    assert logs[0]["error_message"] == "429 slow down"


def test_non_backend_failure_does_not_fall_back(harness: Harness) -> None:
    primary = harness.adapter("primary-stub", _plain_failure("2 tests failed"))
    secondary = harness.adapter("secondary-stub", _ok())
    pipeline_id = _pipeline(
        harness.client, _profiles({"profile": "primary", "fallback": ["secondary"]})
    )

    run, logs = _run(harness.client, pipeline_id)

    assert run["final_status"] == "failed", run
    assert len(primary.tasks) == 1
    assert secondary.tasks == []
    assert [log["runtime_id"] for log in logs] == ["primary-stub"]
    assert _attempts(logs) == [{"attempt": 1, "profile_id": "primary", "error_category": None}]


def test_exhausted_chain_fails_with_every_attempt_audited(harness: Harness) -> None:
    harness.adapter("primary-stub", _backend_error("rate_limit", "429"))
    harness.adapter("secondary-stub", _backend_error("out_of_credits", "quota"))
    harness.adapter("tertiary-stub", _backend_error("unavailable", "503"))
    pipeline_id = _pipeline(
        harness.client,
        _profiles({"profile": "primary", "fallback": ["secondary", "tertiary"]}),
    )

    run, logs = _run(harness.client, pipeline_id)

    assert run["final_status"] == "failed", run
    assert [log["status"] for log in logs] == ["failed", "failed", "failed"]
    assert _attempts(logs) == [
        {"attempt": 1, "profile_id": "primary", "error_category": "rate_limit"},
        {"attempt": 2, "profile_id": "secondary", "error_category": "out_of_credits"},
        {"attempt": 3, "profile_id": "tertiary", "error_category": "unavailable"},
    ]
    state = harness.client.get(f"/runs/{run['id']}/state").json()
    assert "503" in (state["verification_reason"] or "")


def test_profile_without_fallback_behaves_as_before(harness: Harness) -> None:
    primary = harness.adapter("primary-stub", _backend_error("rate_limit", "429"))
    secondary = harness.adapter("secondary-stub", _ok())
    pipeline_id = _pipeline(harness.client, _profiles("primary"))

    run, logs = _run(harness.client, pipeline_id)

    assert run["final_status"] == "failed", run
    assert len(primary.tasks) == 1
    assert secondary.tasks == []
    assert len(logs) == 1
    assert logs[0]["extra_data"] is None


def test_unknown_fallback_id_is_rejected_on_save(harness: Harness) -> None:
    payload = _pipeline_payload(
        _agent(harness.client),
        _profiles({"profile": "primary", "fallback": ["secondary", "ghost"]}),
    )

    response = harness.client.post("/pipelines", json=payload)

    assert response.status_code == 422, response.text
    assert "unknown profile id 'ghost'" in str(response.json()["detail"])


def test_unknown_fallback_id_is_rejected_on_update(harness: Harness) -> None:
    agent_id = _agent(harness.client)
    created = harness.client.post(
        "/pipelines", json=_pipeline_payload(agent_id, _profiles("primary"))
    )
    assert created.status_code == 201, created.text

    response = harness.client.put(
        f"/pipelines/{created.json()['id']}",
        json=_pipeline_payload(agent_id, _profiles({"profile": "primary", "fallback": ["ghost"]})),
    )

    assert response.status_code == 422, response.text
    assert "unknown profile id 'ghost'" in str(response.json()["detail"])


def _bundle(agent_id: str, backend_profiles: dict[str, Any]) -> dict[str, Any]:
    pipeline = _pipeline_payload(agent_id, backend_profiles)
    del pipeline["backend_profiles"]
    return {
        "schema_version": "pipeline-export/2",
        "pipeline": pipeline,
        "backend_profiles": backend_profiles,
    }


def test_unknown_fallback_id_is_rejected_on_import(harness: Harness) -> None:
    bundle = _bundle(
        _agent(harness.client), _profiles({"profile": "primary", "fallback": ["ghost"]})
    )

    response = harness.client.post("/pipelines/import", json=bundle)

    assert response.status_code == 422, response.text
    assert "unknown profile id 'ghost'" in str(response.json()["detail"])


def test_inspection_reports_fallbacks_and_unknown_ids(harness: Harness) -> None:
    bundle = _bundle(
        _agent(harness.client),
        _profiles({"profile": "primary", "fallback": ["secondary", "ghost"]}),
    )

    response = harness.client.post("/pipelines/import/inspect-backends", json=bundle)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overrides"] == {"coder": "primary"}
    assert body["fallbacks"] == {"coder": ["secondary", "ghost"]}
    assert body["unknown_fallbacks"] == ["ghost"]


def test_trigger_rejects_a_stored_unknown_fallback_id(harness: Harness) -> None:
    # A version saved before #935 validated fallbacks can still name a ghost id.
    primary = harness.adapter("primary-stub", _ok())
    pipeline_id = _pipeline(harness.client, _profiles("primary"))
    session_factory = harness.client.app.state.session_factory  # type: ignore[attr-defined]
    with session_factory() as session:
        version = session.scalar(
            select(PipelineVersionORM).where(PipelineVersionORM.pipeline_id == pipeline_id)
        )
        assert version is not None
        version.backend_profiles = _profiles({"profile": "primary", "fallback": ["ghost"]})
        session.commit()

    response = harness.client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {"repo": "demo", "branch": "main"}},
    )

    assert response.status_code == 422, response.text
    assert "unknown profile id 'ghost'" in response.json()["detail"]
    assert primary.tasks == []
