"""Tests for GET /settings — operator dashboard view."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-settings-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_settings_returns_three_sections(client: TestClient) -> None:
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.json()
    assert "runtimes" in body
    assert "providers" in body
    assert "engine" in body


def test_settings_runtimes_lists_all_registered(client: TestClient) -> None:
    body = client.get("/settings").json()
    runtime_ids = {r["id"] for r in body["runtimes"]}
    # All 7 default-registry runtimes should show up.
    assert {
        "api-call",
        "bash",
        "claude-code",
        "gemini-cli",
        "codex",
        "aider",
        "http",
    }.issubset(runtime_ids)


def test_settings_runtime_row_shape(client: TestClient) -> None:
    body = client.get("/settings").json()
    bash = next(r for r in body["runtimes"] if r["id"] == "bash")
    assert "display_name" in bash
    assert "kind" in bash
    assert "available" in bash
    assert isinstance(bash["available"], bool)
    # bash adapter is always available on POSIX boxes — sanity check.
    assert bash["available"] is True


def test_settings_providers_lists_api_call_set(client: TestClient) -> None:
    body = client.get("/settings").json()
    provider_ids = {p["id"] for p in body["providers"]}
    assert {"anthropic", "openai", "openai-compat", "gemini"}.issubset(provider_ids)


def test_settings_provider_configured_reflects_env_state(
    client: TestClient,
) -> None:
    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fake-test-only"
    try:
        body = client.get("/settings").json()
        anthropic = next(p for p in body["providers"] if p["id"] == "anthropic")
        assert anthropic["configured"] is True
        assert anthropic["default_env_var"] == "ANTHROPIC_API_KEY"
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_settings_openai_compat_default_env_is_null(client: TestClient) -> None:
    """openai-compat's API key var is per-agent, so we report it as None."""
    body = client.get("/settings").json()
    compat = next(p for p in body["providers"] if p["id"] == "openai-compat")
    assert compat["default_env_var"] is None
    # And `configured` is always False at the provider level.
    assert compat["configured"] is False


def test_settings_engine_section_has_paths_and_version(client: TestClient) -> None:
    body = client.get("/settings").json()
    engine = body["engine"]
    assert engine["version"]
    assert engine["db_path"].endswith("state.db")
    assert engine["checkpoint_db_path"].endswith("state.checkpoints.db")
    assert isinstance(engine["recursion_limit"], int)
    assert engine["recursion_limit"] > 0
