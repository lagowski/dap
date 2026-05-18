"""Smoke test — ownership enforcement on /runs (#299, sub-A4b2d, closes Phase A).

Mirrors the agents / pipelines / projects ownership suites.

Covered:
- list endpoint filters by ``user_id``
- get / state / state-history / node-log return 404 (not 403) for
  cross-user lookups — anti-enumeration
- trigger via ``POST /runs`` rejects foreign pipeline (404) and
  foreign project (422)
- abort / pause / resume / approve / retry / skip cross-user → 404
- admin sees every user's runs
- audit log captures the ``run.triggered`` event with the acting user_id

Triggering a real run pulls in the LangGraph runner, so we use the
``bash`` adapter (always available, deterministic) for the smoke
suite — same pattern ``test_runs_trigger.py`` uses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AuditLogORM, UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.smoke._auth import authed_test_client, register_and_login


@pytest.fixture
def client(
    engine_config_factory: Callable[..., EngineConfig],
) -> Iterator[TestClient]:
    """Authed engine with bash runtime explicitly enabled for these smoke tests.

    These tests predate the runtime policy and intentionally use the
    deterministic bash adapter to exercise run ownership paths.
    """
    app = create_app(engine_config_factory(allow_bash_runtime_for_non_admin=True))
    with authed_test_client(app) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _agent_payload(name: str = "Run Agent") -> dict[str, Any]:
    return {
        "name": name,
        "role": "task_selector",
        "runtime_id": "bash",
        # Deterministic bash command — no LLM call.
        "runtime_config": {"command": "echo ok"},
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": [],
        "output_schema": [],
        "constraints": [],
        "budget_limit_usd": 1.0,
        "timeout_ms": 30000,
    }


def _pipeline_payload(*, agent_id: str, name: str = "test-pipeline") -> dict[str, Any]:
    return {
        "name": name,
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
    }


def _seed_alice_pipeline(client: TestClient) -> str:
    a = client.post("/agents", json=_agent_payload()).json()["id"]
    return client.post(  # type: ignore[no-any-return]
        "/pipelines", json=_pipeline_payload(agent_id=a)
    ).json()["id"]


def _seed_bob_pipeline(client: TestClient, bob_jwt: str) -> str:
    a = client.post(
        "/agents", json=_agent_payload(name="bob-agent"), headers=_bearer(bob_jwt)
    ).json()["id"]
    return client.post(  # type: ignore[no-any-return]
        "/pipelines",
        json=_pipeline_payload(agent_id=a, name="bob-pipeline"),
        headers=_bearer(bob_jwt),
    ).json()["id"]


def _trigger_run(
    client: TestClient,
    pipeline_id: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Trigger a run and wait for it to finalise (bash adapter is fast)."""
    resp = client.post(
        "/runs",
        json={"pipeline_id": pipeline_id, "initial_state": {}},
        headers=headers or {},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _wait_for_terminal(client: TestClient, run_id: str, *, timeout: float = 5.0) -> str:
    """Poll until the run reaches a terminal status (success/failed/aborted)."""
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        status = client.get(f"/runs/{run_id}").json()["final_status"]
        if status in {"success", "failed", "aborted"}:
            return status  # type: ignore[no-any-return]
        _time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach terminal state within {timeout}s")


def test_list_runs_filtered_to_caller(client: TestClient) -> None:
    """User A's listing must not include runs triggered by User B.

    Listing inspects the ``runs`` row, not its terminal status, so we
    don't bother waiting for finalisation here — the bash-adapter run
    is already on disk (synchronously committed by ``trigger_run``).
    """
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]

    bob_jwt = register_and_login(client, "bob-runs1@example.com")
    bob_pipeline = _seed_bob_pipeline(client, bob_jwt)
    bob_run = _trigger_run(client, bob_pipeline, headers=_bearer(bob_jwt))["id"]

    alice_listing = client.get("/runs").json()
    assert {r["id"] for r in alice_listing["items"]} == {alice_run}

    bob_listing = client.get("/runs", headers=_bearer(bob_jwt)).json()
    bob_ids = {r["id"] for r in bob_listing["items"]}
    assert bob_run in bob_ids
    assert alice_run not in bob_ids


def test_trigger_with_foreign_pipeline_returns_404(client: TestClient) -> None:
    """Bob cannot trigger Alice's pipeline — 404, not "pipeline exists but…"."""
    alice_pipeline = _seed_alice_pipeline(client)

    bob_jwt = register_and_login(client, "bob-runs2@example.com")
    resp = client.post(
        "/runs",
        json={"pipeline_id": alice_pipeline, "initial_state": {}},
        headers=_bearer(bob_jwt),
    )
    assert resp.status_code == 404


def test_trigger_with_foreign_project_returns_422(client: TestClient) -> None:
    """Bob cannot trigger a run scoped to Alice's project — 422 with
    anti-enumeration wording ("Project not found"), same as a missing id."""
    alice_project = client.post(
        "/projects",
        json={
            "name": "alice-proj",
            "description": "",
            "working_directory": None,
            "repo_url": None,
            "default_branch": "main",
            "pipelines": {},
            "env_vars": {},
        },
    ).json()["id"]

    bob_jwt = register_and_login(client, "bob-runs3@example.com")
    bob_pipeline = _seed_bob_pipeline(client, bob_jwt)
    resp = client.post(
        "/runs",
        json={
            "pipeline_id": bob_pipeline,
            "project_id": alice_project,
            "initial_state": {},
        },
        headers=_bearer(bob_jwt),
    )
    assert resp.status_code == 422
    assert "not found" in str(resp.json()["detail"]).lower()


def test_get_other_users_run_returns_404(client: TestClient) -> None:
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]

    bob_jwt = register_and_login(client, "bob-runs4@example.com")
    resp = client.get(f"/runs/{alice_run}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_run_state_endpoints_cross_user_404(client: TestClient) -> None:
    """state / state-history / node-log all 404 for cross-user reads."""
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]
    _wait_for_terminal(client, alice_run)

    bob_jwt = register_and_login(client, "bob-runs5@example.com")
    assert client.get(f"/runs/{alice_run}/state", headers=_bearer(bob_jwt)).status_code == 404
    assert (
        client.get(f"/runs/{alice_run}/state/history", headers=_bearer(bob_jwt)).status_code == 404
    )
    assert client.get(f"/runs/{alice_run}/nodes/n1", headers=_bearer(bob_jwt)).status_code == 404


def test_lifecycle_endpoints_cross_user_404(client: TestClient) -> None:
    """abort / pause / resume / approve / retry / skip all 404 cross-user."""
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]

    bob_jwt = register_and_login(client, "bob-runs6@example.com")
    for path in (
        f"/runs/{alice_run}/abort",
        f"/runs/{alice_run}/pause",
        f"/runs/{alice_run}/resume",
        f"/runs/{alice_run}/nodes/n1/approve",
        f"/runs/{alice_run}/nodes/n1/retry",
        f"/runs/{alice_run}/nodes/n1/skip",
    ):
        resp = client.post(path, headers=_bearer(bob_jwt))
        assert resp.status_code == 404, (path, resp.status_code, resp.text)


def test_unauthenticated_run_endpoints_return_401(client: TestClient) -> None:
    """Every /runs endpoint must require auth."""
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]
    _wait_for_terminal(client, alice_run)

    client.headers.pop("Authorization", None)
    assert client.get("/runs").status_code == 401
    assert (
        client.post("/runs", json={"pipeline_id": alice_pipeline, "initial_state": {}}).status_code
        == 401
    )
    assert client.get(f"/runs/{alice_run}").status_code == 401
    assert client.get(f"/runs/{alice_run}/state").status_code == 401
    assert client.get(f"/runs/{alice_run}/state/history").status_code == 401
    assert client.get(f"/runs/{alice_run}/nodes/n1").status_code == 401
    assert client.post(f"/runs/{alice_run}/abort").status_code == 401
    assert client.post(f"/runs/{alice_run}/pause").status_code == 401
    assert client.post(f"/runs/{alice_run}/resume").status_code == 401
    assert client.post(f"/runs/{alice_run}/nodes/n1/approve").status_code == 401
    assert client.post(f"/runs/{alice_run}/nodes/n1/retry").status_code == 401
    assert client.post(f"/runs/{alice_run}/nodes/n1/skip").status_code == 401


async def test_admin_sees_every_users_runs(client: TestClient) -> None:
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]

    bob_jwt = register_and_login(client, "bob-runs7@example.com")
    bob_pipeline = _seed_bob_pipeline(client, bob_jwt)
    bob_run = _trigger_run(client, bob_pipeline, headers=_bearer(bob_jwt))["id"]

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        admin = (
            (
                await session.execute(
                    select(UserORM).where(UserORM.email == "test@local.dev")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .unique()
            .all()[0]
        )
        admin.is_superuser = True
        await session.commit()

    listing = client.get("/runs").json()
    ids = {r["id"] for r in listing["items"]}
    assert {alice_run, bob_run}.issubset(ids)


async def test_trigger_writes_audit_row(client: TestClient) -> None:
    """``run.triggered`` audit row carries the actor's user_id + pipeline id."""
    alice_pipeline = _seed_alice_pipeline(client)
    alice_run = _trigger_run(client, alice_pipeline)["id"]

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLogORM).where(AuditLogORM.event_type == "run.triggered")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    event = rows[0].event_data
    assert event is not None
    assert event["run_id"] == alice_run
    assert event["pipeline_id"] == alice_pipeline
    assert event["pipeline_version"] == 1
    assert event["trigger_source"] == "api"
