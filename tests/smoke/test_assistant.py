"""Configuration assistant chat endpoint — stub contract (#689 slice 1)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    return authed_client


def test_chat_requires_auth(client_no_auth: TestClient) -> None:
    resp = client_no_auth.post(
        "/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 401


def test_chat_returns_assistant_message(
    client: TestClient, no_provider_env: None
) -> None:
    resp = client.post(
        "/assistant/chat",
        json={"messages": [{"role": "user", "content": "an agent that reviews PRs cheaply"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"]["role"] == "assistant"
    assert isinstance(body["message"]["content"], str)
    assert body["message"]["content"]
    # Stub is ungrounded until the model backend lands.
    assert body["grounded"] is False
    assert body["citations"] == []


def test_chat_rejects_malformed_messages(client: TestClient) -> None:
    # Missing required ``content`` → 422 from the request model.
    resp = client.post("/assistant/chat", json={"messages": [{"role": "user"}]})
    assert resp.status_code == 422


@pytest.fixture
def client_no_auth(engine_config_factory) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    from dap_engine.app import create_app

    app = create_app(engine_config_factory())
    with TestClient(app) as c:
        yield c
