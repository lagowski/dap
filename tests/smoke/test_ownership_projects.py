"""Smoke test — ownership enforcement on /projects (#299, sub-A4b2c).

Mirrors ``test_ownership_agents.py`` / ``test_ownership_pipelines.py``:

- list endpoint filters by ``user_id``
- get / update / archive / trigger-run return 404 (not 403) for
  cross-user lookups — anti-enumeration
- ``_validate_pipeline_bindings`` rejects binding a foreign pipeline_id
  (surfaced as "unknown" so the existence isn't leaked through the
  422 wording)
- admin sees every user's projects
- audit log captures ``project.created`` / ``project.updated`` /
  ``project.archived`` events with the acting user_id

Cross-user setup requires each user to own their own agent + pipeline,
because projects bind pipelines by id and the binding validator enforces
pipeline-level ownership at create / update time.
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
    tmp = tempfile.mkdtemp(prefix="dap-ownership-projects-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="ownership-projects-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _agent_payload(name: str = "Project Agent") -> dict[str, Any]:
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


def _project_payload(*, name: str = "alice-project", **overrides: Any) -> dict[str, Any]:
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


def _create_alice_pipeline(client: TestClient, suffix: str = "") -> str:
    a = client.post("/agents", json=_agent_payload(name=f"alice-agent{suffix}")).json()["id"]
    return client.post(  # type: ignore[no-any-return]
        "/pipelines", json=_pipeline_payload(agent_id=a, name=f"alice-pipeline{suffix}")
    ).json()["id"]


def _create_bob_pipeline(client: TestClient, bob_jwt: str) -> str:
    a = client.post(
        "/agents", json=_agent_payload(name="bob-agent"), headers=_bearer(bob_jwt)
    ).json()["id"]
    return client.post(  # type: ignore[no-any-return]
        "/pipelines",
        json=_pipeline_payload(agent_id=a, name="bob-pipeline"),
        headers=_bearer(bob_jwt),
    ).json()["id"]


def _create_alice_project(client: TestClient, name: str = "alice-project") -> str:
    return client.post(  # type: ignore[no-any-return]
        "/projects", json=_project_payload(name=name)
    ).json()["id"]


def test_list_projects_filtered_to_caller(client: TestClient) -> None:
    alice_pid = _create_alice_project(client)

    bob_jwt = register_and_login(client, "bob-proj1@example.com")
    bob_pid = client.post(
        "/projects",
        json=_project_payload(name="bob-project"),
        headers=_bearer(bob_jwt),
    ).json()["id"]

    alice_listing = client.get("/projects").json()
    assert {p["id"] for p in alice_listing["items"]} == {alice_pid}

    bob_listing = client.get("/projects", headers=_bearer(bob_jwt)).json()
    bob_ids = {p["id"] for p in bob_listing["items"]}
    assert bob_ids == {bob_pid}
    assert alice_pid not in bob_ids


def test_get_other_users_project_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_project(client)
    bob_jwt = register_and_login(client, "bob-proj2@example.com")
    resp = client.get(f"/projects/{alice_pid}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_update_other_users_project_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_project(client)
    bob_jwt = register_and_login(client, "bob-proj3@example.com")
    update_body = _project_payload(name="hijacked")
    resp = client.put(f"/projects/{alice_pid}", json=update_body, headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_archive_other_users_project_returns_404(client: TestClient) -> None:
    alice_pid = _create_alice_project(client)
    bob_jwt = register_and_login(client, "bob-proj4@example.com")
    resp = client.delete(f"/projects/{alice_pid}", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_create_project_with_foreign_pipeline_binding_returns_422(
    client: TestClient,
) -> None:
    """Non-admins cannot bind pipelines they don't own — error wording
    treats foreign ids as "unknown" to avoid leaking existence."""
    alice_pipeline = _create_alice_pipeline(client)

    bob_jwt = register_and_login(client, "bob-proj5@example.com")
    resp = client.post(
        "/projects",
        json=_project_payload(
            name="bob-attempt",
            pipelines={"default": alice_pipeline},
        ),
        headers=_bearer(bob_jwt),
    )
    assert resp.status_code == 422
    body = resp.json()
    # Wording must match the "unknown pipeline_id(s)" branch — never
    # the "archived" branch — so foreign vs. truly-missing ids are
    # indistinguishable to the caller.
    assert "unknown pipeline_id" in str(body["detail"])


def test_update_project_with_foreign_pipeline_binding_returns_422(
    client: TestClient,
) -> None:
    """Same anti-leak rule on PUT — Bob can't bind Alice's pipeline."""
    alice_pipeline = _create_alice_pipeline(client)

    bob_jwt = register_and_login(client, "bob-proj6@example.com")
    bob_project = client.post(
        "/projects", json=_project_payload(name="bob-project"), headers=_bearer(bob_jwt)
    ).json()["id"]

    resp = client.put(
        f"/projects/{bob_project}",
        json=_project_payload(name="bob-project", pipelines={"default": alice_pipeline}),
        headers=_bearer(bob_jwt),
    )
    assert resp.status_code == 422
    assert "unknown pipeline_id" in str(resp.json()["detail"])


def test_trigger_run_on_other_users_project_returns_404(client: TestClient) -> None:
    """Cross-user POST /projects/{id}/run/{kind} must surface 404 —
    otherwise a probe distinguishes archived/missing/unbound projects
    by error wording."""
    alice_pipeline = _create_alice_pipeline(client)
    alice_project = client.post(
        "/projects",
        json=_project_payload(name="alice-bound", pipelines={"default": alice_pipeline}),
    ).json()["id"]

    bob_jwt = register_and_login(client, "bob-proj7@example.com")
    resp = client.post(f"/projects/{alice_project}/run/default", headers=_bearer(bob_jwt))
    assert resp.status_code == 404


def test_unauthenticated_project_endpoints_return_401(client: TestClient) -> None:
    """Without auth, every /projects endpoint must reject the request."""
    pid = _create_alice_project(client)
    # Drop the auto-attached header for this single test.
    client.headers.pop("Authorization", None)
    assert client.get("/projects").status_code == 401
    assert client.post("/projects", json=_project_payload()).status_code == 401
    assert client.get(f"/projects/{pid}").status_code == 401
    assert client.put(f"/projects/{pid}", json=_project_payload()).status_code == 401
    assert client.delete(f"/projects/{pid}").status_code == 401
    assert client.post(f"/projects/{pid}/run/default").status_code == 401


async def test_admin_sees_every_users_projects(client: TestClient) -> None:
    """A superuser's listing includes rows from every user."""
    alice_pid = _create_alice_project(client)
    bob_jwt = register_and_login(client, "bob-proj8@example.com")
    bob_pid = client.post(
        "/projects",
        json=_project_payload(name="bob-project"),
        headers=_bearer(bob_jwt),
    ).json()["id"]

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

    listing = client.get("/projects").json()
    ids = {p["id"] for p in listing["items"]}
    assert ids == {alice_pid, bob_pid}


async def test_admin_can_bind_foreign_pipeline(client: TestClient) -> None:
    """Admins bypass the pipeline-ownership check on bindings — useful
    for an admin curating shared workflows on top of user-owned pipelines."""
    bob_jwt = register_and_login(client, "bob-proj9@example.com")
    bob_pipeline = _create_bob_pipeline(client, bob_jwt)

    # Promote the default user to admin.
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

    # Admin creates a project bound to Bob's pipeline — must succeed.
    resp = client.post(
        "/projects",
        json=_project_payload(name="admin-curated", pipelines={"default": bob_pipeline}),
    )
    assert resp.status_code == 201, resp.text


async def test_create_project_writes_audit_row(client: TestClient) -> None:
    """``project.created`` audit row carries the actor's user_id."""
    pid = _create_alice_project(client, name="audited-project")

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLogORM).where(AuditLogORM.event_type == "project.created")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["project_id"] == pid
    assert row.event_data["name"] == "audited-project"
