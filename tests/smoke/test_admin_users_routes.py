"""Smoke test — admin users list endpoint (#301, sub-C2).

Covers ``GET /users`` (the list endpoint added in sub-C2 — fastapi-users
only ships ``GET /users/{id}`` / ``PATCH`` / ``DELETE``). Per-user
mutations are tested implicitly through the existing fastapi-users
upstream suite; we don't reproduce that here.

Verifies:
- Admins get a paginated list of every user.
- Non-admins get 404 (anti-enumeration — same as fastapi-users'
  ``/users/{id}`` response for non-admins probing foreign ids).
- Anonymous callers get 401.
- ``include_deleted=false`` (default) hides soft-deleted users;
  ``true`` surfaces them.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import UserORM
from fastapi.testclient import TestClient

PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-admin-users-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="admin-users-smoke-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _register(c: TestClient, email: str) -> str:
    """Register a user, return their id."""
    resp = c.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]  # type: ignore[no-any-return]


def _login(c: TestClient, email: str) -> str:
    """Log in, return a bearer token."""
    resp = c.post("/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]  # type: ignore[no-any-return]


def _promote_to_admin(app: Any, email: str) -> None:
    """Flip ``is_superuser`` on the user with this email via direct DB write.

    ``asyncio.run`` (not ``get_event_loop().run_until_complete``) — the
    latter raises ``DeprecationWarning`` on 3.10+ and breaks outright
    if a loop is already running on the calling thread.
    """
    import asyncio

    from sqlalchemy import select

    async def _do() -> None:
        async with app.state.async_session_factory() as session:
            row = (
                (await session.execute(select(UserORM).where(UserORM.email == email)))  # type: ignore[arg-type]
                .scalars()
                .unique()
                .one()
            )
            row.is_superuser = True
            await session.commit()

    asyncio.run(_do())


def test_list_users_returns_401_for_anonymous(client: TestClient) -> None:
    resp = client.get("/users")
    assert resp.status_code == 401


def test_list_users_returns_404_for_non_admin(client: TestClient) -> None:
    """Anti-enumeration: a normal user must not learn that the endpoint
    exists. fastapi-users' ``/users/{id}`` follows the same rule."""
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_list_users_returns_paginated_list_for_admin(client: TestClient) -> None:
    """Happy path — admin sees every user."""
    _register(client, "alice@example.com")
    _register(client, "bob@example.com")
    _register(client, "carol@example.com")
    _promote_to_admin(client.app, "alice@example.com")

    token = _login(client, "alice@example.com")
    resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["offset"] == 0
    assert body["limit"] == 50
    emails = {item["email"] for item in body["items"]}
    assert emails == {"alice@example.com", "bob@example.com", "carol@example.com"}
    # Each row carries the admin-only fields.
    sample = body["items"][0]
    assert {"id", "email", "is_active", "is_superuser", "is_verified"} <= sample.keys()
    assert "created_at" in sample  # not exposed in /users/{id}


def test_list_users_paging(client: TestClient) -> None:
    """``offset`` + ``limit`` clamp the result window."""
    for n in range(5):
        _register(client, f"user{n}@example.com")
    _promote_to_admin(client.app, "user0@example.com")
    token = _login(client, "user0@example.com")

    resp = client.get(
        "/users?limit=2&offset=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2


def test_list_users_excludes_soft_deleted_by_default(client: TestClient) -> None:
    """``deleted_at != NULL`` rows are hidden unless ``include_deleted=true``."""
    _register(client, "alice@example.com")
    _register(client, "ghost@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    # fastapi-users' DELETE is soft via our UserManager.delete override.
    ghost_resp = client.get(
        "/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ghost_resp.status_code == 200, ghost_resp.text
    ghost_id = next(
        item["id"] for item in ghost_resp.json()["items"] if item["email"] == "ghost@example.com"
    )
    delete_resp = client.delete(
        f"/users/{ghost_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_resp.status_code in (200, 204), delete_resp.text

    # Default: ghost is gone.
    resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
    emails = {item["email"] for item in resp.json()["items"]}
    assert emails == {"alice@example.com"}

    # include_deleted=true: ghost back.
    resp_with_deleted = client.get(
        "/users?include_deleted=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    emails_with = {item["email"] for item in resp_with_deleted.json()["items"]}
    assert emails_with == {"alice@example.com", "ghost@example.com"}
