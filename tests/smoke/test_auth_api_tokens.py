"""Smoke test — API tokens CRUD + auth (#299, sub-A3).

Covers:
- Create / list / revoke happy path
- Newly created ``dap_*`` token authenticates ``GET /users/me``
- Revoked token is rejected
- Expired token is rejected
- API token cannot manage other API tokens (JWT-only management surface)
- Listing returns only the caller's tokens — never another user's
- DELETE on a token belonging to another user returns 404 (not 403),
  so the endpoint can't be used to enumerate other users' token IDs
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

TEST_PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-smoke-api-tokens-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="api-tokens-smoke-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _register_and_login(client: TestClient, email: str) -> str:
    """Register a user and return the JWT bearer string.

    Asserts on each step's status code so a setup-stage failure
    surfaces as a clear AssertionError instead of a confusing
    KeyError when ``json()["access_token"]`` is read off a 4xx
    response body.
    """
    register = client.post(
        "/auth/register",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert register.status_code == 201, register.text
    login = client.post(
        "/auth/jwt/login",
        data={"username": email, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]  # type: ignore[no-any-return]


def test_create_token_returns_raw_value_once(client: TestClient) -> None:
    """POST /auth/api-tokens returns the raw token; GET never does."""
    jwt = _register_and_login(client, "alice@example.com")
    headers = {"Authorization": f"Bearer {jwt}"}

    create = client.post(
        "/auth/api-tokens",
        json={"name": "ci-pipeline"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    raw_token = body["token"]
    assert raw_token.startswith("dap_")
    # Prefix is stored separately and listable.
    assert body["prefix"] == raw_token[len("dap_") : len("dap_") + 8]
    assert body["name"] == "ci-pipeline"
    assert body["revoked_at"] is None
    assert body["last_used_at"] is None

    # The list endpoint must NOT include the raw token.
    listing = client.get("/auth/api-tokens", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert rows[0]["prefix"] == body["prefix"]


def test_api_token_authenticates_users_me(client: TestClient) -> None:
    """A freshly minted dap_* token can call GET /users/me directly."""
    jwt = _register_and_login(client, "bob@example.com")
    raw = client.post(
        "/auth/api-tokens",
        json={"name": "laptop"},
        headers={"Authorization": f"Bearer {jwt}"},
    ).json()["token"]

    me = client.get("/users/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "bob@example.com"


def test_revoked_token_is_rejected(client: TestClient) -> None:
    jwt = _register_and_login(client, "carol@example.com")
    auth = {"Authorization": f"Bearer {jwt}"}
    create = client.post("/auth/api-tokens", json={"name": "to-revoke"}, headers=auth).json()
    raw = create["token"]
    token_id = create["id"]

    # Confirm it works.
    assert client.get("/users/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 200

    # Revoke.
    revoke = client.delete(f"/auth/api-tokens/{token_id}", headers=auth)
    assert revoke.status_code == 204

    # Now it must not authenticate.
    me = client.get("/users/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 401


async def test_expired_token_is_rejected(client: TestClient) -> None:
    """An expired token (manually back-dated) is treated as revoked."""
    from dap_engine.persistence.models import ApiTokenORM
    from sqlalchemy import select

    jwt = _register_and_login(client, "dave@example.com")
    auth = {"Authorization": f"Bearer {jwt}"}
    create = client.post(
        "/auth/api-tokens",
        json={"name": "short-lived", "expires_in_days": 1},
        headers=auth,
    ).json()
    raw = create["token"]

    # Force-expire by writing a past expires_at via the async session.
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(ApiTokenORM).where(ApiTokenORM.id == create["id"])
                )
            )
            .scalars()
            .one()
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    me = client.get("/users/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 401


def test_api_token_cannot_manage_api_tokens(client: TestClient) -> None:
    """The token CRUD surface is JWT-only — leaked CLI creds can't escalate."""
    jwt = _register_and_login(client, "eve@example.com")
    raw = client.post(
        "/auth/api-tokens",
        json={"name": "primary"},
        headers={"Authorization": f"Bearer {jwt}"},
    ).json()["token"]

    api_auth = {"Authorization": f"Bearer {raw}"}

    # The dap_ token authenticates /users/me but NOT /auth/api-tokens
    # CRUD endpoints, which are restricted to the JWT backend.
    create = client.post("/auth/api-tokens", json={"name": "sibling"}, headers=api_auth)
    assert create.status_code == 401
    listing = client.get("/auth/api-tokens", headers=api_auth)
    assert listing.status_code == 401


def test_list_scoped_to_caller_only(client: TestClient) -> None:
    """Listing returns only the caller's tokens, never another user's."""
    jwt_alice = _register_and_login(client, "alice2@example.com")
    jwt_bob = _register_and_login(client, "bob2@example.com")
    client.post(
        "/auth/api-tokens",
        json={"name": "alice-token"},
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    client.post(
        "/auth/api-tokens",
        json={"name": "bob-token-1"},
        headers={"Authorization": f"Bearer {jwt_bob}"},
    )
    client.post(
        "/auth/api-tokens",
        json={"name": "bob-token-2"},
        headers={"Authorization": f"Bearer {jwt_bob}"},
    )

    alice_listing = client.get(
        "/auth/api-tokens",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    ).json()
    assert {row["name"] for row in alice_listing} == {"alice-token"}

    bob_listing = client.get(
        "/auth/api-tokens",
        headers={"Authorization": f"Bearer {jwt_bob}"},
    ).json()
    assert {row["name"] for row in bob_listing} == {"bob-token-1", "bob-token-2"}


def test_delete_other_users_token_returns_404(client: TestClient) -> None:
    """Cross-user DELETE looks like 'doesn't exist' — no enumeration."""
    jwt_alice = _register_and_login(client, "alice3@example.com")
    jwt_bob = _register_and_login(client, "bob3@example.com")

    bob_token_id = client.post(
        "/auth/api-tokens",
        json={"name": "bob-private"},
        headers={"Authorization": f"Bearer {jwt_bob}"},
    ).json()["id"]

    resp = client.delete(
        f"/auth/api-tokens/{bob_token_id}",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    # 404, not 403 — same response as a non-existent ID.
    assert resp.status_code == 404
