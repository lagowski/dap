"""Smoke test — admin audit log read endpoint (#301, sub-C3).

Verifies ``GET /audit/events``:
- Auth gate: 401 anonymous, 404 non-admin, 200 admin.
- Returns events triggered by ``/auth/register`` (``user.registered``)
  and ``/auth/jwt/login`` (``user.logged_in``) — confirms the auth
  hooks from sub-A4b1 actually write rows.
- Filters: ``event_type`` and ``user_id`` narrow the result set.
- Pagination: ``offset`` + ``limit`` clamp the window.
- Ordering: rows are ``created_at DESC``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from dap_engine.persistence.models import UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "test-password-123"


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


def test_audit_events_returns_401_for_anonymous(client: TestClient) -> None:
    assert client.get("/audit/events").status_code == 401


def test_audit_events_returns_404_for_non_admin(client: TestClient) -> None:
    """Anti-enumeration: a normal user must not learn the endpoint exists."""
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.get("/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_audit_events_returns_recorded_events_for_admin(client: TestClient) -> None:
    """Happy path — register + login produce two audit rows."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")  # → user.logged_in

    resp = client.get("/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    types = [row["event_type"] for row in body["items"]]
    # Both event types from the auth hooks should be present. We don't
    # pin the exact count because future hooks may add more — the
    # important invariant is "register + login fire".
    assert "user.registered" in types
    assert "user.logged_in" in types


def test_audit_events_filters_by_event_type(client: TestClient) -> None:
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get(
        "/audit/events?event_type=user.registered",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(row["event_type"] == "user.registered" for row in body["items"])


def test_audit_events_filters_by_user_id(client: TestClient) -> None:
    """Filter pin-points one user's events even when others exist."""
    alice_id = _register(client, "alice@example.com")
    _register(client, "bob@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get(
        f"/audit/events?user_id={alice_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(row["user_id"] == alice_id for row in body["items"])
    # Bob's register event must NOT appear. ``event_data`` is nullable
    # so use ``(... or {})`` instead of ``.get("event_data", {})`` —
    # the latter would raise on rows where the column is explicitly
    # ``None`` (system events, pre-auth failures).
    bob_events = [
        row
        for row in body["items"]
        if (row.get("event_data") or {}).get("email") == "bob@example.com"
    ]
    assert bob_events == []


def test_audit_events_paginates(client: TestClient) -> None:
    """``offset`` + ``limit`` clamp the window."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get(
        "/audit/events?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 1
    assert len(body["items"]) == 1


def test_audit_events_orders_newest_first(client: TestClient) -> None:
    """``created_at DESC`` — the most recent event sits at index 0."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    # Two logins — the second is the most recent event.
    _login(client, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get(
        "/audit/events?event_type=user.logged_in",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 2
    # ISO 8601 strings are lexicographically comparable, so a
    # straight string comparison suffices.
    assert items[0]["created_at"] >= items[1]["created_at"]


def _admin_token(client: TestClient) -> str:
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    return _login(client, "alice@example.com")


def test_audit_events_filters_by_event_type_prefix(client: TestClient) -> None:
    """``event_type_prefix`` matches a whole family (e.g. all ``user.*``)."""
    token = _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/audit/events?event_type_prefix=user.", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 2
    assert all(row["event_type"].startswith("user.") for row in body["items"])

    # A prefix that matches no family returns an empty page (not an error).
    empty = client.get("/audit/events?event_type_prefix=agent.", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


def test_audit_events_prefix_escapes_like_wildcards(client: TestClient) -> None:
    """A ``%`` in the prefix is treated literally, not as a SQL wildcard."""
    token = _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # "user.%" would match every "user." event if % weren't escaped; no
    # event type contains a literal '%', so the correct result is 0.
    resp = client.get("/audit/events?event_type_prefix=user.%25", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_audit_events_filters_by_created_range(client: TestClient) -> None:
    """``created_from`` / ``created_to`` bound the window by timestamp."""
    token = _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    now = datetime.now(UTC)
    # quote() so the "+00:00" offset isn't decoded as a space by the query
    # parser (the dashboard's URLSearchParams encodes it the same way).
    past = quote((now - timedelta(hours=1)).isoformat())
    future = quote((now + timedelta(hours=1)).isoformat())

    # Everything is in the past hour → from=past returns the events …
    from_past = client.get(f"/audit/events?created_from={past}", headers=headers)
    assert from_past.status_code == 200
    assert from_past.json()["total"] >= 2

    # … but from=future excludes them all.
    from_future = client.get(f"/audit/events?created_from={future}", headers=headers)
    assert from_future.status_code == 200
    assert from_future.json()["total"] == 0

    # to=past also excludes everything that just happened.
    to_past = client.get(f"/audit/events?created_to={past}", headers=headers)
    assert to_past.status_code == 200
    assert to_past.json()["total"] == 0
