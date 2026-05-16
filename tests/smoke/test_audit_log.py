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
    from dap_engine.persistence.models import UserORM
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
        manager = UserManager(SQLAlchemyUserDatabase(session, UserORM))
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
