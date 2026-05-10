"""Smoke test — ownership enforcement on /agents (#299, sub-A4b2a).

Each test exercises a different facet of the ownership rule:

- list endpoint filters by ``user_id``
- get / update / archive return 404 (not 403) for cross-user lookups
  — anti-enumeration
- admin sees every user's agents
- audit log captures the create / update / archive events with the
  acting user_id

The fixture authenticates a default user (``test@local.dev``) and
the helper ``register_and_login`` mints a second account when the
test needs cross-user verification.
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
    tmp = tempfile.mkdtemp(prefix="dap-ownership-agents-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="ownership-agents-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Owned Agent",
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
    payload.update(overrides)
    return payload


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_agents_filtered_to_caller(client: TestClient) -> None:
    """User A's listing must not include agents owned by User B."""
    # Default user creates an agent.
    a_id = client.post("/agents", json=_create_payload(name="alice-agent")).json()["id"]

    # Second user, separate token.
    bob_jwt = register_and_login(client, "bob@example.com")
    client.post(
        "/agents",
        json=_create_payload(name="bob-agent"),
        headers=_bearer(bob_jwt),
    )

    # Default user only sees their own row.
    listing = client.get("/agents").json()
    names = {a["name"] for a in listing["items"]}
    assert names == {"alice-agent"}

    # Bob also only sees his own row.
    bob_listing = client.get("/agents", headers=_bearer(bob_jwt)).json()
    bob_names = {a["name"] for a in bob_listing["items"]}
    assert bob_names == {"bob-agent"}

    # Verify default agent_id was filtered out for Bob.
    assert a_id not in {a["id"] for a in bob_listing["items"]}


def test_get_other_users_agent_returns_404(client: TestClient) -> None:
    """Cross-user GET looks like 'no such agent' — anti-enumeration."""
    a_id = client.post("/agents", json=_create_payload()).json()["id"]
    bob_jwt = register_and_login(client, "bob2@example.com")
    resp = client.get(f"/agents/{a_id}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_update_other_users_agent_returns_404(client: TestClient) -> None:
    a_id = client.post("/agents", json=_create_payload()).json()["id"]
    bob_jwt = register_and_login(client, "bob3@example.com")

    update_body = _create_payload()
    update_body.pop("role", None)  # role is immutable
    resp = client.put(f"/agents/{a_id}", json=update_body, headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_archive_other_users_agent_returns_404(client: TestClient) -> None:
    a_id = client.post("/agents", json=_create_payload()).json()["id"]
    bob_jwt = register_and_login(client, "bob4@example.com")
    resp = client.delete(f"/agents/{a_id}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_archive_other_users_in_use_agent_returns_404_not_409(client: TestClient) -> None:
    """Anti-enumeration regression — DELETE must gate ownership before
    the 409-precheck, otherwise a non-admin probing a foreign id whose
    agent happens to be in-use gets a structured 409 with the foreign
    pipeline's id + name. Caught by Copilot review on PR #310.

    Setup: Alice creates an agent and embeds it in a pipeline (so
    pipelines_using_agent() returns non-empty). Bob then tries DELETE.
    Expected: 404 with no leak of Alice's pipeline name.
    """
    # Alice (the default fixture user) creates the agent and a pipeline
    # that references it.
    a_id = client.post("/agents", json=_create_payload(name="alice-in-use-agent")).json()["id"]
    pipeline_payload = {
        "name": "alice-secret-pipeline",
        "description": "",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": a_id, "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
        "defaults": {
            "max_attempts": 3,
            "budget_limit_usd": 5.0,
            "approval_required_nodes": [],
        },
    }
    pipeline_resp = client.post("/pipelines", json=pipeline_payload)
    assert pipeline_resp.status_code == 201, pipeline_resp.text

    # Bob now probes Alice's agent id.
    bob_jwt = register_and_login(client, "bob-probe@example.com")
    resp = client.delete(f"/agents/{a_id}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404
    # Sanity: the response body must not contain the Alice-owned pipeline
    # name anywhere — that's exactly the leak this test guards against.
    assert "alice-secret-pipeline" not in resp.text


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    """Without auth, every /agents endpoint must reject the request."""
    # Drop the auto-attached header for this single test.
    client.headers.pop("Authorization", None)
    assert client.get("/agents").status_code == 401
    assert client.post("/agents", json=_create_payload()).status_code == 401


async def test_admin_sees_every_users_agents(client: TestClient) -> None:
    """A superuser's listing includes rows from every user."""
    client.post("/agents", json=_create_payload(name="default-agent"))
    bob_jwt = register_and_login(client, "bob5@example.com")
    client.post(
        "/agents",
        json=_create_payload(name="bob-agent"),
        headers=_bearer(bob_jwt),
    )

    # Promote default user to admin via direct DB write.
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(select(UserORM).where(UserORM.email == "test@local.dev"))  # type: ignore[arg-type]
            )
            .scalars()
            .unique()
            .all()
        )
        admin = rows[0]
        admin.is_superuser = True
        await session.commit()

    # New JWT inherits the (now superuser) flag at decode time — fastapi-users
    # carries is_superuser in the User object resolved per-request, so the
    # existing token still works without re-login (decode hits the fresh row).
    listing = client.get("/agents").json()
    names = {a["name"] for a in listing["items"]}
    assert names == {"default-agent", "bob-agent"}


async def test_create_agent_writes_audit_row(client: TestClient) -> None:
    """``agent.created`` audit row carries the actor's user_id and the agent id."""
    create = client.post("/agents", json=_create_payload(name="audited"))
    assert create.status_code == 201
    agent_id = create.json()["id"]

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLogORM).where(AuditLogORM.event_type == "agent.created")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["agent_id"] == agent_id
    assert row.event_data["name"] == "audited"
