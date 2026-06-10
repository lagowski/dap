"""End-to-end tests for assistant interaction logging + admin browse (#722).

The assistant endpoint stores the **redacted** transcript + reply in the
interaction log; admins browse it at ``GET /interactions``. Non-admins
get 404 (same anti-enumeration shape as ``/audit``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import InteractionLogORM, UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.smoke._auth import (
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    authed_test_client,
)

# A realistic GitHub-token shape — must never appear in stored content.
FAKE_TOKEN = "ghp_" + "a1B2" * 9


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


async def _promote_default_user_to_admin(client: TestClient) -> None:
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
        rows[0].is_superuser = True
        await session.commit()


def _chat(client: TestClient, content: str) -> None:
    resp = client.post(
        "/assistant/chat",
        json={"messages": [{"role": "user", "content": content}]},
    )
    assert resp.status_code == 200, resp.text


async def test_assistant_chat_stores_redacted_interaction(client: TestClient) -> None:
    _chat(client, f"my token is {FAKE_TOKEN}, how do I configure the project?")
    await _promote_default_user_to_admin(client)

    resp = client.get("/interactions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["surface"] == "assistant"
    assert item["user_id"] is not None
    assert item["redacted_response"]  # the (stub) reply text is stored

    # The pasted token must be redacted everywhere in the stored record.
    assert FAKE_TOKEN not in resp.text
    request_contents = " ".join(m["content"] for m in item["redacted_request"])
    assert "[REDACTED:GITHUB_TOKEN]" in request_contents


def test_interactions_is_admin_only(client: TestClient) -> None:
    """Non-admin must get 404 — the route doesn't exist for them."""
    resp = client.get("/interactions")
    assert resp.status_code == 404


async def test_interactions_supports_surface_filter_and_pagination(
    client: TestClient,
) -> None:
    _chat(client, "first question")
    _chat(client, "second question")
    await _promote_default_user_to_admin(client)

    resp = client.get("/interactions", params={"limit": 1, "offset": 0})
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1

    resp = client.get("/interactions", params={"surface": "run"})
    assert resp.json()["total"] == 0


async def test_interaction_logging_can_be_disabled(
    engine_config_factory: Callable[..., EngineConfig],
) -> None:
    config = engine_config_factory(interaction_log_enabled=False)
    app = create_app(config)
    with authed_test_client(app) as client:
        _chat(client, "should not be recorded")
        await _promote_default_user_to_admin(client)
        resp = client.get("/interactions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


def _insert_backdated_interaction(app: Any, days_old: int) -> None:
    factory = app.state.session_factory
    with factory() as session:
        row = InteractionLogORM(
            surface="assistant",
            redacted_request=[{"role": "user", "content": "old"}],
            redacted_response="old reply",
        )
        session.add(row)
        session.flush()
        row.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_old)
        session.commit()


async def test_startup_purges_rows_past_retention(
    engine_config_factory: Callable[..., EngineConfig],
) -> None:
    """Rows older than the retention window are purged on engine startup."""
    config = engine_config_factory(interaction_log_retention_days=30)
    app = create_app(config)
    with authed_test_client(app) as client:
        await _promote_default_user_to_admin(client)
        _insert_backdated_interaction(client.app, days_old=100)
        _insert_backdated_interaction(client.app, days_old=1)

    # Second boot against the same DB — startup purge runs in the lifespan.
    # The user already exists, so log in instead of re-registering.
    app2 = create_app(config)
    with TestClient(app2) as client:
        login = client.post(
            "/auth/jwt/login",
            data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
        )
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        body = client.get("/interactions").json()
        assert body["total"] == 1
