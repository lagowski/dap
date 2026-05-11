"""Smoke test — admin API token routes (#301, sub-C4).

Covers the two admin-wide endpoints added in sub-C4:
- ``GET  /auth/api-tokens/admin``  — list every user's tokens
- ``DELETE /auth/api-tokens/admin/{id}`` — revoke any user's token

The per-user CRUD (``POST`` / ``GET`` / ``DELETE`` against own tokens)
is exercised by ``test_auth_api_tokens.py``; this file only adds the
admin-only paths.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-admin-api-tokens-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="admin-api-tokens-smoke-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _register(c: TestClient, email: str) -> str:
    resp = c.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]  # type: ignore[no-any-return]


def _login(c: TestClient, email: str) -> str:
    resp = c.post("/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]  # type: ignore[no-any-return]


def _promote_to_admin(app: Any, email: str) -> None:
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_token(c: TestClient, jwt: str, name: str) -> str:
    """Mint a token for the caller, return its id."""
    resp = c.post(
        "/auth/api-tokens",
        json={"name": name},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]  # type: ignore[no-any-return]


def test_admin_list_returns_401_for_anonymous(client: TestClient) -> None:
    assert client.get("/auth/api-tokens/admin").status_code == 401


def test_admin_list_returns_404_for_non_admin(client: TestClient) -> None:
    """Anti-enumeration: non-admin must not learn the endpoint exists."""
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.get("/auth/api-tokens/admin", headers=_bearer(token))
    assert resp.status_code == 404


def test_admin_list_returns_every_users_tokens(client: TestClient) -> None:
    """Admin sees tokens minted by every user, with owner email
    attached so the table can render the owner column without a
    per-row roundtrip."""
    _register(client, "alice@example.com")
    bob_id = _register(client, "bob@example.com")
    _promote_to_admin(client.app, "alice@example.com")

    alice_jwt = _login(client, "alice@example.com")
    bob_jwt = _login(client, "bob@example.com")
    _create_token(client, alice_jwt, "alice-laptop")
    _create_token(client, bob_jwt, "bob-laptop")

    resp = client.get("/auth/api-tokens/admin", headers=_bearer(alice_jwt))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"alice-laptop", "bob-laptop"}
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["bob-laptop"]["owner_email"] == "bob@example.com"
    assert by_name["bob-laptop"]["owner_id"] == bob_id
    # ``prefix`` is the 8-char ``token_prefix`` index (the chars after
    # ``dap_`` in the raw token, see ``auth/api_tokens.py``) — not the
    # full token. Length is the spec-given invariant.
    assert len(by_name["bob-laptop"]["prefix"]) == 8


def test_admin_list_include_revoked_default_true(client: TestClient) -> None:
    """``include_revoked=true`` (default) keeps revoked tokens in the
    list so admins can see the full lifecycle. ``false`` filters them
    out."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    alice_jwt = _login(client, "alice@example.com")

    keep_id = _create_token(client, alice_jwt, "keep-me")
    revoked_id = _create_token(client, alice_jwt, "revoke-me")
    # User-scoped revoke — uses the existing self-revoke endpoint.
    revoke_resp = client.delete(f"/auth/api-tokens/{revoked_id}", headers=_bearer(alice_jwt))
    assert revoke_resp.status_code == 204

    all_resp = client.get("/auth/api-tokens/admin", headers=_bearer(alice_jwt))
    ids = {item["id"] for item in all_resp.json()["items"]}
    assert ids == {keep_id, revoked_id}

    active_resp = client.get(
        "/auth/api-tokens/admin?include_revoked=false",
        headers=_bearer(alice_jwt),
    )
    active_ids = {item["id"] for item in active_resp.json()["items"]}
    assert active_ids == {keep_id}


def test_admin_revoke_returns_404_for_non_admin(client: TestClient) -> None:
    """Non-admin cannot revoke someone else's token via the admin
    endpoint — the route shouldn't even exist to them (404)."""
    _register(client, "alice@example.com")
    _register(client, "bob@example.com")
    bob_jwt = _login(client, "bob@example.com")
    bob_token_id = _create_token(client, bob_jwt, "bob-laptop")

    alice_jwt = _login(client, "alice@example.com")  # not admin
    resp = client.delete(f"/auth/api-tokens/admin/{bob_token_id}", headers=_bearer(alice_jwt))
    assert resp.status_code == 404


def test_admin_revoke_any_users_token(client: TestClient) -> None:
    """Admin can soft-revoke any user's token; the row stays in the
    DB (audit-friendly) with ``revoked_at`` set."""
    _register(client, "alice@example.com")
    _register(client, "bob@example.com")
    _promote_to_admin(client.app, "alice@example.com")

    bob_jwt = _login(client, "bob@example.com")
    bob_token_id = _create_token(client, bob_jwt, "bob-laptop")

    alice_jwt = _login(client, "alice@example.com")
    resp = client.delete(f"/auth/api-tokens/admin/{bob_token_id}", headers=_bearer(alice_jwt))
    assert resp.status_code == 204

    # Listing now shows the token as revoked.
    listing = client.get("/auth/api-tokens/admin", headers=_bearer(alice_jwt)).json()
    bob_token = next(item for item in listing["items"] if item["id"] == bob_token_id)
    assert bob_token["revoked_at"] is not None


def test_admin_revoke_unknown_token_returns_404(client: TestClient) -> None:
    """Hitting the endpoint with a real-looking UUID that doesn't
    exist still 404s — no information leak about which IDs are taken."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    alice_jwt = _login(client, "alice@example.com")
    import uuid as _uuid

    resp = client.delete(f"/auth/api-tokens/admin/{_uuid.uuid4()}", headers=_bearer(alice_jwt))
    assert resp.status_code == 404
