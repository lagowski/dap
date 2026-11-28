"""Smoke test — audit log entries on auth events (#299, sub-A4b1).

Verifies that the audit-log table receives entries for the events the
admin panel needs to surface in Phase C: register, login, API-token
create / revoke, and soft-delete. Each test triggers the action via
the public HTTP API and reads the audit row back through the same
async session factory the engine uses, so the test exercises the
real write path (no mocked-out SessionLocal).
"""

from __future__ import annotations

from dap_engine.persistence.models import AuditLogORM
from fastapi.testclient import TestClient
from sqlalchemy import select

TEST_PASSWORD = "test-password-123"


async def _audit_rows(client: TestClient, event_type: str | None = None) -> list[AuditLogORM]:
    """Read audit rows via the engine's async session factory."""
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        stmt = select(AuditLogORM).order_by(AuditLogORM.created_at)
        if event_type is not None:
            stmt = stmt.where(AuditLogORM.event_type == event_type)
        return list((await session.execute(stmt)).scalars().all())


async def test_register_writes_audit_row(client: TestClient) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": TEST_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    rows = await _audit_rows(client, "user.registered")
    assert len(rows) == 1
    row = rows[0]
    assert str(row.user_id) == user_id
    assert row.event_data is not None
    assert row.event_data["email"] == "alice@example.com"


async def test_login_writes_audit_row(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": TEST_PASSWORD},
    )
    login = client.post(
        "/auth/jwt/login",
        data={"username": "bob@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200, login.text

    rows = await _audit_rows(client, "user.logged_in")
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["email"] == "bob@example.com"


async def test_api_token_create_writes_audit_row(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "carol@example.com", "password": TEST_PASSWORD},
    )
    jwt = client.post(
        "/auth/jwt/login",
        data={"username": "carol@example.com", "password": TEST_PASSWORD},
    ).json()["access_token"]
    create = client.post(
        "/auth/api-tokens",
        json={"name": "ci"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert create.status_code == 201, create.text
    token_id = create.json()["id"]

    rows = await _audit_rows(client, "api_token.created")
    assert len(rows) == 1
    assert rows[0].event_data is not None
    assert rows[0].event_data["token_id"] == token_id
    assert rows[0].event_data["name"] == "ci"


async def test_api_token_revoke_writes_audit_row(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "dave@example.com", "password": TEST_PASSWORD},
    )
    jwt = client.post(
        "/auth/jwt/login",
        data={"username": "dave@example.com", "password": TEST_PASSWORD},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}
    create = client.post("/auth/api-tokens", json={"name": "to-revoke"}, headers=headers).json()
    token_id = create["id"]

    revoke = client.delete(f"/auth/api-tokens/{token_id}", headers=headers)
    assert revoke.status_code == 204

    rows = await _audit_rows(client, "api_token.revoked")
    assert len(rows) == 1
    assert rows[0].event_data is not None
    assert rows[0].event_data["token_id"] == token_id


async def test_soft_delete_user_writes_audit_row(client: TestClient) -> None:
    """UserManager.delete (soft delete from sub-A1) should now also audit."""
    from dap_engine.auth.users import UserManager
    from dap_engine.persistence.models import OAuthAccountORM, UserORM
    from fastapi_users.db import SQLAlchemyUserDatabase
    from sqlalchemy import select as sa_select

    client.post(
        "/auth/register",
        json={"email": "eve@example.com", "password": TEST_PASSWORD},
    )

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        user = (
            (await session.execute(sa_select(UserORM).where(UserORM.email == "eve@example.com")))  # type: ignore[arg-type]
            .scalars()
            .unique()
            .one()
        )
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM, OAuthAccountORM))
        await manager.delete(user)
        # `manager.delete` already commits via ``user_db.update`` AND via
        # the audit-event helper (eager commit per its docstring). The
        # explicit commit here is defensive — at this point the session
        # has nothing pending, so it's a no-op, kept only so a future
        # change that adds further work in this block doesn't silently
        # forget to flush.

    rows = await _audit_rows(client, "user.deleted")
    assert len(rows) == 1
    assert rows[0].event_data is not None
    assert rows[0].event_data["email"] == "eve@example.com"
    assert rows[0].event_data["soft"] is True


# ---------------------------------------------------------------------------
# E5 — password-change + OAuth-link audit events
# ---------------------------------------------------------------------------


async def test_password_change_writes_audit_row(client: TestClient) -> None:
    """Self-service password rotation via PATCH /users/me audits as
    ``user.password_changed`` (distinct from ``user.password_reset``)."""
    client.post(
        "/auth/register",
        json={"email": "frank@example.com", "password": TEST_PASSWORD},
    )
    jwt = client.post(
        "/auth/jwt/login",
        data={"username": "frank@example.com", "password": TEST_PASSWORD},
    ).json()["access_token"]

    new_password = "rotated-password-456"
    patch = client.patch(
        "/users/me",
        json={"password": new_password},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert patch.status_code == 200, patch.text

    rows = await _audit_rows(client, "user.password_changed")
    assert len(rows) == 1
    assert rows[0].event_data is not None
    assert rows[0].event_data["email"] == "frank@example.com"
    # The audit row MUST NEVER carry the password value itself —
    # belt-and-braces check that the only stored field is ``email``.
    assert "password" not in rows[0].event_data
    assert "hashed_password" not in rows[0].event_data

    # And as a confidence check: the new password actually works.
    re_login = client.post(
        "/auth/jwt/login",
        data={"username": "frank@example.com", "password": new_password},
    )
    assert re_login.status_code == 200, re_login.text


async def test_profile_update_without_password_does_not_audit(
    client: TestClient,
) -> None:
    """A PATCH /users/me without ``password`` field is profile noise.

    ``on_after_update`` keys on the presence of ``password`` in the
    update_dict — non-password updates (email, etc.) must NOT write
    a ``user.password_changed`` row.
    """
    client.post(
        "/auth/register",
        json={"email": "grace@example.com", "password": TEST_PASSWORD},
    )
    jwt = client.post(
        "/auth/jwt/login",
        data={"username": "grace@example.com", "password": TEST_PASSWORD},
    ).json()["access_token"]

    # Update something innocuous — fastapi-users default schema lets
    # us pass an empty update_dict (no-op patch) which still goes
    # through ``update()`` and fires ``on_after_update``.
    patch = client.patch(
        "/users/me",
        json={},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert patch.status_code == 200, patch.text

    rows = await _audit_rows(client, "user.password_changed")
    assert rows == [], (
        f"Profile update without password field wrote audit rows: {[r.event_data for r in rows]}"
    )


async def test_oauth_link_to_existing_user_writes_audit_row(
    client: TestClient,
) -> None:
    """Linking a NEW OAuth provider to an EXISTING user audits as
    ``oauth.linked``. We invoke ``oauth_callback`` directly here
    rather than running a real OAuth dance — the audit semantics are
    a property of the UserManager hook, not of the HTTP route, and
    the upstream fastapi-users test suite already covers the route
    plumbing end-to-end.
    """
    import uuid

    from dap_engine.auth.users import UserManager
    from dap_engine.persistence.models import OAuthAccountORM, UserORM
    from fastapi_users.db import SQLAlchemyUserDatabase

    # Register a password-account user first — this is the user
    # whom we'll later "link" a Google account to.
    register = client.post(
        "/auth/register",
        json={"email": "henry@example.com", "password": TEST_PASSWORD},
    )
    assert register.status_code == 201, register.text

    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM, OAuthAccountORM))
        await manager.oauth_callback(
            oauth_name="google",
            access_token="fake-access-token-not-used-by-test",
            account_id=f"google-account-{uuid.uuid4()}",
            account_email="henry@example.com",
            associate_by_email=True,
        )

    rows = await _audit_rows(client, "oauth.linked")
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["provider"] == "google"
    assert row.event_data["account_email"] == "henry@example.com"
    # The audit row MUST NEVER carry the access_token / refresh_token.
    assert "access_token" not in row.event_data
    assert "refresh_token" not in row.event_data


async def test_oauth_callback_refresh_does_not_audit(
    client: TestClient,
) -> None:
    """Token refresh on an already-linked OAuth account is NOT
    audited — it's just credential rotation, no new attack surface."""
    import uuid

    from dap_engine.auth.users import UserManager
    from dap_engine.persistence.models import OAuthAccountORM, UserORM
    from fastapi_users.db import SQLAlchemyUserDatabase

    client.post(
        "/auth/register",
        json={"email": "ivy@example.com", "password": TEST_PASSWORD},
    )

    account_id = f"google-account-{uuid.uuid4()}"
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]

    # First call — links the Google account to the existing user.
    # Audits as ``oauth.linked`` (1 row written).
    async with factory() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM, OAuthAccountORM))
        await manager.oauth_callback(
            oauth_name="google",
            access_token="first-token",
            account_id=account_id,
            account_email="ivy@example.com",
            associate_by_email=True,
        )

    # Second call — same (provider, account_id) pair. This is a
    # token refresh (case 1). Must NOT write another audit row.
    async with factory() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM, OAuthAccountORM))
        await manager.oauth_callback(
            oauth_name="google",
            access_token="second-rotated-token",
            account_id=account_id,
            account_email="ivy@example.com",
            associate_by_email=True,
        )

    rows = await _audit_rows(client, "oauth.linked")
    assert len(rows) == 1, (
        f"Expected exactly 1 oauth.linked row from first call only, got: "
        f"{[r.event_data for r in rows]}"
    )
