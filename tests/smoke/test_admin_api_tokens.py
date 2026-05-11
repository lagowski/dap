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
from datetime import UTC, datetime, timedelta
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
    assert all_resp.status_code == 200, all_resp.text
    ids = {item["id"] for item in all_resp.json()["items"]}
    assert ids == {keep_id, revoked_id}

    active_resp = client.get(
        "/auth/api-tokens/admin?include_revoked=false",
        headers=_bearer(alice_jwt),
    )
    assert active_resp.status_code == 200, active_resp.text
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


def test_admin_revoke_writes_audit_row_with_acting_admin(
    client: TestClient,
) -> None:
    """The admin revoke must persist an audit row that distinguishes
    self-revocations from admin interventions — acting admin in
    ``user_id``, target owner in ``event_data.target_user_id``, plus
    ``by_admin: true``. The PR description calls this out; lock it
    in with a test (Copilot review on PR #330)."""
    alice_id = _register(client, "alice@example.com")
    bob_id = _register(client, "bob@example.com")
    _promote_to_admin(client.app, "alice@example.com")

    bob_jwt = _login(client, "bob@example.com")
    bob_token_id = _create_token(client, bob_jwt, "bob-laptop")

    alice_jwt = _login(client, "alice@example.com")
    revoke = client.delete(f"/auth/api-tokens/admin/{bob_token_id}", headers=_bearer(alice_jwt))
    assert revoke.status_code == 204, revoke.text

    # The acting-admin's view of the audit log shows the row.
    audit = client.get("/audit/events?event_type=api_token.revoked", headers=_bearer(alice_jwt))
    assert audit.status_code == 200, audit.text
    by_admin_rows = [
        row
        for row in audit.json()["items"]
        if (row.get("event_data") or {}).get("by_admin") is True
    ]
    assert len(by_admin_rows) == 1
    row = by_admin_rows[0]
    assert row["user_id"] == alice_id
    assert row["event_data"]["target_user_id"] == bob_id
    assert row["event_data"]["token_id"] == bob_token_id
    assert row["event_data"]["name"] == "bob-laptop"


def test_admin_endpoints_reject_api_token_bearer(client: TestClient) -> None:
    """The admin token-management routes are JWT-only by design — an
    API token must not be able to enumerate or revoke other API
    tokens. The user-scoped routes share that constraint (sub-A3);
    this test pins it down for the admin variants (Copilot review on
    PR #330)."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    alice_jwt = _login(client, "alice@example.com")

    # Mint a token, then capture its raw value.
    mint = client.post(
        "/auth/api-tokens",
        json={"name": "alice-laptop"},
        headers=_bearer(alice_jwt),
    )
    assert mint.status_code == 201, mint.text
    raw_token = mint.json()["token"]
    token_id = mint.json()["id"]
    assert raw_token.startswith("dap_")

    # Now try to hit the admin endpoints with the api-token bearer.
    # ``current_active_user_jwt_only`` only accepts JWTs; the API
    # token backend doesn't recognise it on this route so we expect
    # 401 (the same rejection user-scoped routes give).
    list_resp = client.get(
        "/auth/api-tokens/admin", headers={"Authorization": f"Bearer {raw_token}"}
    )
    assert list_resp.status_code == 401, list_resp.text

    revoke_resp = client.delete(
        f"/auth/api-tokens/admin/{token_id}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert revoke_resp.status_code == 401, revoke_resp.text


def test_admin_list_include_revoked_false_hides_expired(
    client: TestClient,
) -> None:
    """``include_revoked=false`` means "currently valid" — must also
    hide tokens whose ``expires_at`` has passed (Copilot review on
    PR #330). Without this, the toggle's wording would lie.

    We hand-craft an expired row via the async session factory since
    the create endpoint clamps ``expires_in_days`` to >=1."""
    from dap_engine.persistence.models import ApiTokenORM

    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    alice_jwt = _login(client, "alice@example.com")
    keep_id = _create_token(client, alice_jwt, "still-valid")

    async def _seed_expired() -> str:
        async with client.app.state.async_session_factory() as session:  # type: ignore[attr-defined]
            # Use the same generate_token shape so the row is realistic.
            from dap_engine.auth.api_tokens import generate_token
            from sqlalchemy import select

            alice = (
                (
                    await session.execute(
                        select(UserORM).where(UserORM.email == "alice@example.com")  # type: ignore[arg-type]
                    )
                )
                .scalars()
                .unique()
                .one()
            )
            gen = generate_token()
            row = ApiTokenORM(
                user_id=alice.id,
                name="expired-token",
                token_prefix=gen.prefix,
                token_hash=gen.sha256_hash,
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return str(row.id)

    expired_id = asyncio.run(_seed_expired())

    # include_revoked=true (default) — admin sees the expired row.
    all_resp = client.get("/auth/api-tokens/admin", headers=_bearer(alice_jwt))
    assert all_resp.status_code == 200
    all_ids = {item["id"] for item in all_resp.json()["items"]}
    assert expired_id in all_ids
    assert keep_id in all_ids

    # include_revoked=false — expired row hidden too.
    active_resp = client.get(
        "/auth/api-tokens/admin?include_revoked=false",
        headers=_bearer(alice_jwt),
    )
    assert active_resp.status_code == 200
    active_ids = {item["id"] for item in active_resp.json()["items"]}
    assert active_ids == {keep_id}
