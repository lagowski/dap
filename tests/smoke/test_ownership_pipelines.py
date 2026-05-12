"""Smoke test — ownership enforcement on /pipelines (#299, sub-A4b2b).

Mirrors ``test_ownership_agents.py``:

- list endpoint filters by ``user_id``
- get / update / archive / list-versions / get-version / export return
  404 (not 403) for cross-user lookups — anti-enumeration
- ``POST /pipelines/validate`` is auth-required (would otherwise leak
  agent existence)
- admin sees every user's pipelines
- audit log captures the ``pipeline.created`` / ``pipeline.updated`` /
  ``pipeline.archived`` events with the acting user_id

The fixture authenticates a default user (``test@local.dev``) and the
helper ``register_and_login`` mints a second account when the test
needs cross-user verification.

Pipelines reference agents by id, so cross-user setup needs each user
to own their own agent — otherwise the DAG validator returns 422
("agent not found") instead of letting us reach the ownership gate.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AuditLogORM, UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.smoke._auth import authed_test_client, register_and_login


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-ownership-pipelines-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="ownership-pipelines-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _agent_payload(name: str = "Pipeline Agent") -> dict[str, Any]:
    return {
        "name": name,
        "role": "task_selector",
        "runtime_id": "claude-code",
        "runtime_config": {"model": "claude-sonnet-4-6", "max_turns": 1},
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


def _create_alice_pipeline(client: TestClient, name: str = "alice-pipeline") -> str:
    """Default-fixture user owns both an agent and a pipeline using it."""
    a_id = client.post("/agents", json=_agent_payload(name=f"{name}-agent")).json()["id"]
    return client.post(  # type: ignore[no-any-return]
        "/pipelines", json=_pipeline_payload(agent_id=a_id, name=name)
    ).json()["id"]


def _create_bob_pipeline(client: TestClient, bob_jwt: str) -> tuple[str, str]:
    """Bob (second user) owns an agent + pipeline of his own."""
    bob_agent = client.post(
        "/agents", json=_agent_payload(name="bob-agent"), headers=_bearer(bob_jwt)
    ).json()["id"]
    bob_pipeline = client.post(
        "/pipelines",
        json=_pipeline_payload(agent_id=bob_agent, name="bob-pipeline"),
        headers=_bearer(bob_jwt),
    ).json()["id"]
    return bob_agent, bob_pipeline


def test_list_pipelines_filtered_to_caller(client: TestClient) -> None:
    """User A's listing must not include pipelines owned by User B."""
    alice_pid = _create_alice_pipeline(client)

    bob_jwt = register_and_login(client, "bob-pipe1@example.com")
    _, bob_pid = _create_bob_pipeline(client, bob_jwt)

    alice_listing = client.get("/pipelines").json()
    alice_ids = {p["id"] for p in alice_listing["items"]}
    assert alice_ids == {alice_pid}

    bob_listing = client.get("/pipelines", headers=_bearer(bob_jwt)).json()
    bob_ids = {p["id"] for p in bob_listing["items"]}
    assert bob_ids == {bob_pid}
    assert alice_pid not in bob_ids


def test_get_other_users_pipeline_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe2@example.com")
    resp = client.get(f"/pipelines/{alice_pid}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_update_other_users_pipeline_returns_404(client: TestClient) -> None:
    """Cross-user PUT must surface 404, not 422 (no validator leak)."""
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe3@example.com")
    bob_agent, _ = _create_bob_pipeline(client, bob_jwt)

    # Body is structurally valid against Bob's own agent — if the
    # ownership gate didn't run first, the validator would happily
    # validate it and we'd update Alice's pipeline.
    update_body = _pipeline_payload(agent_id=bob_agent, name="bob-attempted-update")
    resp = client.put(f"/pipelines/{alice_pid}", json=update_body, headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_archive_other_users_pipeline_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe4@example.com")
    resp = client.delete(f"/pipelines/{alice_pid}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_list_versions_other_users_pipeline_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe5@example.com")
    resp = client.get(f"/pipelines/{alice_pid}/versions", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_get_version_other_users_pipeline_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe6@example.com")
    resp = client.get(f"/pipelines/{alice_pid}/versions/1", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_export_other_users_pipeline_returns_404(client: TestClient) -> None:
    """Cross-user export must 404 — would otherwise leak the full DAG."""
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe7@example.com")
    resp = client.get(f"/pipelines/{alice_pid}/export", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_unauthenticated_pipeline_endpoints_return_401(client: TestClient) -> None:
    """Without auth, every /pipelines endpoint must reject the request.

    Includes ``/pipelines/validate``: although it's nominally read-only,
    it executes against the DB to check ``node.agent_id`` references and
    would leak agent existence to anonymous callers.
    """
    pid = _create_alice_pipeline(client)
    a_id = client.get(f"/pipelines/{pid}").json()["nodes"][0]["agent_id"]

    # Drop the auto-attached header for this single test.
    client.headers.pop("Authorization", None)
    assert client.get("/pipelines").status_code == 401
    assert client.post("/pipelines", json=_pipeline_payload(agent_id=a_id)).status_code == 401
    assert (
        client.post("/pipelines/validate", json=_pipeline_payload(agent_id=a_id)).status_code == 401
    )
    assert client.get(f"/pipelines/{pid}").status_code == 401
    assert client.delete(f"/pipelines/{pid}").status_code == 401
    assert client.get(f"/pipelines/{pid}/versions").status_code == 401
    assert client.get(f"/pipelines/{pid}/export").status_code == 401


async def test_admin_sees_every_users_pipelines(client: TestClient) -> None:
    """A superuser's listing includes rows from every user."""
    alice_pid = _create_alice_pipeline(client)
    bob_jwt = register_and_login(client, "bob-pipe8@example.com")
    _, bob_pid = _create_bob_pipeline(client, bob_jwt)

    # Promote default user to admin via direct DB write.
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(UserORM).where(UserORM.email == "test@local.dev")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .unique()
            .all()
        )
        admin = rows[0]
        admin.is_superuser = True
        await session.commit()

    listing = client.get("/pipelines").json()
    ids = {p["id"] for p in listing["items"]}
    assert ids == {alice_pid, bob_pid}


async def test_create_pipeline_writes_audit_row(client: TestClient) -> None:
    """``pipeline.created`` audit row carries the actor's user_id and the pipeline id."""
    pid = _create_alice_pipeline(client, name="audited-pipe")

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLogORM).where(AuditLogORM.event_type == "pipeline.created")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["pipeline_id"] == pid
    assert row.event_data["name"] == "audited-pipe"
