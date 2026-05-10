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
