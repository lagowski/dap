"""Tests for async run lifecycle — abort, stale recovery on startup."""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    PipelineORM,
    PipelineVersionORM,
    RunORM,
    UserORM,
)
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 5.0


class SlowStubAdapter:
    """Adapter that sleeps to simulate long-running LLM calls (gives time to abort)."""

    id = "slow-stub"
    display_name = "Slow Stub"
    kind: RuntimeKind = "api"

    def __init__(self, sleep_seconds: float = 1.0) -> None:
        self.sleep_seconds = sleep_seconds

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, _task: RuntimeTask) -> RuntimeResult:
        await asyncio.sleep(self.sleep_seconds)
        return RuntimeResult(
            success=True, output="slow", duration_ms=int(self.sleep_seconds * 1000)
        )


def _project_payload(*, name: str = "lifecycle-test-project", **overrides: Any) -> dict[str, Any]:
    """Build a minimal POST /projects payload.

    Mirrors the helper of the same name in ``test_ownership_projects.py``;
    duplicated here to avoid cross-test-file import coupling (importing
    private helpers from sibling test modules is a code smell). Move to
    a shared ``tests/smoke/_helpers.py`` if a third test file needs the
    same shape.
    """
    payload: dict[str, Any] = {
        "name": name,
        "description": "",
        "working_directory": None,
        "repo_url": None,
        "default_branch": "main",
        "pipelines": {},
        "env_vars": {},
    }
    payload.update(overrides)
    return payload


def _create_agent(client: TestClient, runtime_id: str = "slow-stub") -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Lifecycle Test",
            "role": "task_selector",
            "runtime_id": runtime_id,
            "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str) -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": "Lifecycle Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.fixture
def slow_client() -> Iterator[tuple[TestClient, RuntimeRegistry]]:
    tmp = tempfile.mkdtemp(prefix="dap-lifecycle-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        registry.register(SlowStubAdapter(sleep_seconds=2.0))
        yield c, registry


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


def test_post_runs_returns_immediately(slow_client: tuple[TestClient, RuntimeRegistry]) -> None:
    """Even with a 2s adapter, POST /runs returns well before the adapter finishes.

    The guarantee is that the endpoint is **async** — the response
    lands before the registered adapter's ``sleep_seconds=2.0`` is
    over. The threshold is set generously vs. the adapter duration:
    a synchronous code path would block ~2s and trip the assert,
    while a healthy async path completes in milliseconds.

    Earlier the threshold was 500ms; that flaked on busy CI runners
    (one run came in at 0.517s, failing despite still proving async
    behaviour). 1.0s keeps the semantic guarantee (2× faster than
    adapter sleep — synchronous would still be detected) with
    double the CI headroom.
    """
    client, _ = slow_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    start = time.monotonic()
    response = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    )
    elapsed = time.monotonic() - start

    assert response.status_code == 201
    body = response.json()
    assert body["final_status"] == "running"
    assert elapsed < 1.0, f"POST /runs took {elapsed:.3f}s — should be async!"

    # Wait for completion to clean up (avoid leaking the task)
    _wait_for_status(client, body["id"], {"success", "failed", "aborted"})


def test_abort_running_run(slow_client: tuple[TestClient, RuntimeRegistry]) -> None:
    client, _ = slow_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]
    assert triggered["final_status"] == "running"

    # Give the adapter a beat to actually start before aborting
    time.sleep(0.1)

    abort_response = client.post(f"/runs/{run_id}/abort")
    assert abort_response.status_code == 200

    # Final status should be aborted (or already final if super-fast)
    completed = _wait_for_status(client, run_id, {"aborted", "success", "failed"})
    assert completed["final_status"] == "aborted"
    assert completed["ended_at"] is not None


def test_abort_already_completed_returns_409(
    slow_client: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = slow_client
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)

    triggered = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
    ).json()
    run_id = triggered["id"]

    # Wait for natural completion
    _wait_for_status(client, run_id, {"success", "failed"})

    # Now abort should 409
    response = client.post(f"/runs/{run_id}/abort")
    assert response.status_code == 409


def test_abort_unknown_run_returns_404(
    slow_client: tuple[TestClient, RuntimeRegistry],
) -> None:
    client, _ = slow_client
    response = client.post("/runs/nonexistent-run-id/abort")
    assert response.status_code == 404


def test_stale_running_runs_marked_failed_on_startup() -> None:
    """A Run row left in 'running' state from a crashed engine should be
    reaped on the next startup (engine has no in-memory task for it)."""
    tmp = tempfile.mkdtemp(prefix="dap-stale-")
    db_path = Path(tmp) / "state.db"
    config = EngineConfig(
        db_path=str(db_path),
        auth_jwt_secret="stale-test-secret",
    )

    # First app instance — write a stale running row + dependencies.
    app = create_app(config)
    with TestClient(app):
        factory = app.state.session_factory
        with factory() as session:
            now = datetime.now(UTC)
            agent = AgentORM(
                id="ghost-agent",
                name="Ghost",
                role="task_selector",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            agent_v = AgentVersionORM(
                id="ghost-agent-v1",
                agent_id="ghost-agent",
                version=1,
                name="Ghost",
                runtime_id="api-call",
                runtime_config={},
                prompt_template="<agent_prompt/>",
                input_schema=[],
                output_schema=[],
                constraints=[],
                budget_limit_usd=None,
                timeout_ms=10_000,
                created_at=now,
            )
            pipeline = PipelineORM(
                id="ghost-pipe",
                name="Ghost Pipe",
                description="",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            pipeline_v = PipelineVersionORM(
                id="ghost-pipe-v1",
                pipeline_id="ghost-pipe",
                version=1,
                name="Ghost Pipe",
                description="",
                schema_version="langgraph/1.0",
                state_schema_ref="PipelineState.v1",
                entry_point="n1",
                nodes=[],
                edges=[],
                defaults={
                    "max_attempts": 3,
                    "budget_limit_usd": 5.0,
                    "approval_required_nodes": [],
                },
                created_at=now,
            )
            stale = RunORM(
                id="stale-run-id",
                pipeline_id="ghost-pipe",
                pipeline_version=1,
                trigger_source="api",
                initial_state={
                    "run_id": "stale-run-id",
                    "repo": "",
                    "branch": "",
                    "available_issues": [],
                    "selected_issue_ids": [],
                    "tests_generated": False,
                    "test_files": [],
                    "test_generation_errors": [],
                    "max_attempts": 3,
                    "attempt": 0,
                    "tests_passed": False,
                    "last_test_output": "",
                    "modified_files": [],
                    "implementation_notes": None,
                    "verification_status": "pending",
                    "verification_reason": None,
                    "final_status": "running",
                },
                current_node=None,
                node_statuses={},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
            )
            session.add(agent)
            session.add(agent_v)
            session.add(pipeline)
            session.add(pipeline_v)
            session.add(stale)
            session.commit()

    # Restart — startup should mark the stale run as failed. The
    # stale-run check itself is user-agnostic (engine startup hook),
    # so we register + log in as a fresh admin to read it back via
    # the API. ``authed_test_client`` mints a default user; promote
    # to admin so the listing surfaces legacy NULL-user runs.
    app2 = create_app(config)
    with authed_test_client(app2) as client2:
        factory2 = app2.state.session_factory
        with factory2() as session2:
            user_orm = session2.query(UserORM).filter(UserORM.email == "test@local.dev").one()
            user_orm.is_superuser = True
            session2.commit()
        login = client2.post(
            "/auth/jwt/login",
            data={"username": "test@local.dev", "password": "test-password-123"},
        )
        client2.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        run = client2.get("/runs/stale-run-id").json()
        assert run["final_status"] == "failed"
        assert run["ended_at"] is not None


def test_post_runs_self_fix_dangerous_project_returns_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatching against a project on the self-fix-dangerous denylist returns 403.

    Background: the 2026-06-01 incident reproduced a destructive failure mode where
    dispatching cortex against the DAP project itself ran pytest fixtures that
    wiped the entire app DB. The fix is a server-side dispatch-deny on a small
    set of UUIDs declared as ``_SELF_FIX_DANGEROUS_PROJECT_IDS`` in ``api/runs.py``.

    This test uses monkeypatch to add the test-created project's UUID to that
    set so the assertion doesn't depend on the production UUID being in the
    test DB (which it isn't and shouldn't be). The PRODUCTION protection is
    the constant's hardcoded entry; the TEST verifies the *mechanism* works
    for any UUID added to the set.
    """
    agent_id = _create_agent(client)
    pipeline_id = _create_pipeline(client, agent_id)
    project_response = client.post(
        "/projects",
        json=_project_payload(pipelines={"develop": pipeline_id}),
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    # Make this project "dangerous" for the duration of this test.
    monkeypatch.setattr(
        "dap_engine.api.runs._SELF_FIX_DANGEROUS_PROJECT_IDS",
        frozenset({project_id}),
    )

    response = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "project_id": project_id,
            "initial_state": {},
        },
    )
    assert response.status_code == 403, f"expected 403, got {response.status_code}: {response.text}"
    detail = response.json()["detail"]
    assert isinstance(detail, dict), f"detail should be structured: {detail!r}"
    assert detail["code"] == "dispatch_denied_self_fix_dangerous"
    assert detail["project_id"] == project_id
    assert "2026-06-01" in detail["message"]
