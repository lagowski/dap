"""Smoke test — password auth happy path (#299, Phase A sub-PR 1).

Covers the register → login → /users/me round-trip end-to-end via the
TestClient. OAuth, API tokens, route ownership protection, and admin
checks are deferred to follow-up sub-PRs.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

# Strong-enough fake secret for the test suite. fastapi-users' default
# password validator requires >= 8 chars and rejects pure-numeric.
TEST_PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-smoke-auth-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        # Deterministic JWT secret keeps token signatures comparable
        # across re-runs — useful when debugging a flaky failure.
        auth_jwt_secret="smoke-test-secret-do-not-use-in-prod",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_register_login_me_roundtrip(client: TestClient) -> None:
    """A freshly registered user can log in and read their own profile."""
    # Register
    register_resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": TEST_PASSWORD},
    )
    assert register_resp.status_code == 201, register_resp.text
    user = register_resp.json()
    assert user["email"] == "alice@example.com"
    assert user["is_active"] is True
    assert user["is_superuser"] is False
    assert "id" in user

    # Log in (fastapi-users JWT login uses OAuth2-form-style: username + password fields)
    login_resp = client.post(
        "/auth/jwt/login",
        data={"username": "alice@example.com", "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200, login_resp.text
    token_payload = login_resp.json()
    assert token_payload["token_type"] == "bearer"
    assert token_payload["access_token"]
    token = token_payload["access_token"]

    # Authenticated /users/me
    me_resp = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200, me_resp.text
    me = me_resp.json()
    assert me["email"] == "alice@example.com"
    assert me["id"] == user["id"]


def test_me_requires_authentication(client: TestClient) -> None:
    """Without a bearer token, /users/me must reject the request."""
    resp = client.get("/users/me")
    assert resp.status_code == 401


def test_login_with_wrong_password_fails(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": TEST_PASSWORD},
    )
    resp = client.post(
        "/auth/jwt/login",
        data={"username": "bob@example.com", "password": "wrong-password"},
    )
    # fastapi-users returns 400 with detail "LOGIN_BAD_CREDENTIALS" rather
    # than 401 — see fastapi-users docs/usage/flow.md.
    assert resp.status_code == 400, resp.text
    assert "LOGIN_BAD_CREDENTIALS" in resp.text


def test_register_duplicate_email_fails(client: TestClient) -> None:
    payload = {"email": "carol@example.com", "password": TEST_PASSWORD}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/auth/register", json=payload)
    assert second.status_code == 400, second.text
    assert "REGISTER_USER_ALREADY_EXISTS" in second.text


async def test_user_manager_delete_is_soft(client: TestClient) -> None:
    """``UserManager.delete`` must soft-delete: row stays, ``is_active=False``.

    Verified via the manager directly because the HTTP ``DELETE /users/{id}``
    endpoint exposed by fastapi-users requires a superuser token, and admin
    bootstrap isn't wired in this sub-PR. The HTTP-level coverage lands with
    Phase C (admin endpoint) once superuser promotion exists.

    After ``UserManager.delete``: the JWT no longer authenticates
    (``current_active_user`` rejects ``is_active=False``), re-login with
    the same credentials returns ``LOGIN_BAD_CREDENTIALS`` (fastapi-users'
    ``authenticate()`` rejects deactivated users), and a fresh signup
    with the same email is rejected as duplicate (row preserved).
    """
    from dap_engine.auth.users import UserManager
    from dap_engine.persistence.models import UserORM
    from fastapi_users.db import SQLAlchemyUserDatabase
    from sqlalchemy import select

    client.post(
        "/auth/register",
        json={"email": "dave@example.com", "password": TEST_PASSWORD},
    )
    login = client.post(
        "/auth/jwt/login",
        data={"username": "dave@example.com", "password": TEST_PASSWORD},
    )
    token = login.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # Soft-delete via the manager directly (admin HTTP path = Phase C).
    # TestClient.app is the Starlette ASGI callable; the underlying FastAPI
    # app (where lifespan attaches state) is reachable via .app.app on the
    # nested wrapper we use for tests. Ignore the typing here — TestClient
    # types are deliberately lax about ASGI app introspection.
    app_state = client.app.state  # type: ignore[attr-defined]
    factory = app_state.async_session_factory
    async with factory() as session:
        user_orm = (
            await session.execute(select(UserORM).where(UserORM.email == "dave@example.com"))  # type: ignore[arg-type]
        ).scalar_one()
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM))
        await manager.delete(user_orm)

    # 1. The same JWT must no longer authenticate — current_user(active=True)
    #    rejects deactivated users even with a still-valid signature.
    me_resp = client.get("/users/me", headers=auth_header)
    assert me_resp.status_code == 401

    # 2. Re-login with the same credentials is rejected as bad-credentials
    #    (authenticate() returns None for is_active=False).
    relogin = client.post(
        "/auth/jwt/login",
        data={"username": "dave@example.com", "password": TEST_PASSWORD},
    )
    assert relogin.status_code == 400, relogin.text
    assert "LOGIN_BAD_CREDENTIALS" in relogin.text

    # 3. A fresh signup with the same email is rejected as duplicate —
    #    the row hasn't gone away.
    duplicate = client.post(
        "/auth/register",
        json={"email": "dave@example.com", "password": TEST_PASSWORD},
    )
    assert duplicate.status_code == 400, duplicate.text
    assert "REGISTER_USER_ALREADY_EXISTS" in duplicate.text
