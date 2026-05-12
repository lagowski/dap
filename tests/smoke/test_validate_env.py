"""Tests for POST /projects/validate-env (#350)."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-validate-env-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


def _mock_httpx_client(*, status_code: int = 200, json_body: dict | None = None,
                        side_effect: Exception | None = None):
    """Build a patched httpx.AsyncClient context manager mock."""
    mock_instance = AsyncMock()
    if side_effect is not None:
        mock_instance.get = AsyncMock(side_effect=side_effect)
    else:
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_body or {}
        mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    ctx = patch("dap_engine.api.projects.httpx.AsyncClient", return_value=mock_instance)
    return ctx


# --------------------------------------------------------------------------
# Endpoint existence
# --------------------------------------------------------------------------


def test_validate_env_endpoint_exists(client: TestClient) -> None:
    resp = client.post("/projects/validate-env", json={"env_vars": {"FOO": "bar"}})
    assert resp.status_code == 200


# --------------------------------------------------------------------------
# Non-token keys pass through
# --------------------------------------------------------------------------


def test_validate_env_no_tokens_passes_through(client: TestClient) -> None:
    resp = client.post(
        "/projects/validate-env",
        json={"env_vars": {"WORKSPACE": "demo", "DEBUG": "1"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    for result in body["results"]:
        assert result["is_token"] is False
        assert result["valid"] is None
        assert result["login"] is None
        assert result["error"] is None


# --------------------------------------------------------------------------
# Token prefix detection
# --------------------------------------------------------------------------


def test_validate_env_returns_is_token_true_for_ghp_prefix(client: TestClient) -> None:
    with _mock_httpx_client(status_code=200, json_body={"login": "octocat"}):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"MY_TOKEN": "ghp_abc123"}},
        )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["is_token"] is True


def test_validate_env_github_pat_prefix_detected(client: TestClient) -> None:
    with _mock_httpx_client(status_code=200, json_body={"login": "user1"}):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"TOKEN": "github_pat_abc123"}},
        )
    result = resp.json()["results"][0]
    assert result["is_token"] is True


def test_validate_env_gho_prefix_detected(client: TestClient) -> None:
    with _mock_httpx_client(status_code=200, json_body={"login": "org-bot"}):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"ORG_TOKEN": "gho_xyz789"}},
        )
    result = resp.json()["results"][0]
    assert result["is_token"] is True


# --------------------------------------------------------------------------
# Valid token
# --------------------------------------------------------------------------


def test_validate_env_valid_token_returns_login(client: TestClient) -> None:
    with _mock_httpx_client(status_code=200, json_body={"login": "octocat"}):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"GH_TOKEN": "ghp_validtoken123"}},
        )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["is_token"] is True
    assert result["valid"] is True
    assert result["login"] == "octocat"
    assert result["error"] is None


# --------------------------------------------------------------------------
# Invalid token (401)
# --------------------------------------------------------------------------


def test_validate_env_invalid_token_returns_401_error(client: TestClient) -> None:
    with _mock_httpx_client(status_code=401):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"GH_TOKEN": "ghp_badtoken"}},
        )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["is_token"] is True
    assert result["valid"] is False
    assert result["login"] is None
    assert "401" in result["error"]


# --------------------------------------------------------------------------
# Network error — degrades to warning, not 500
# --------------------------------------------------------------------------


def test_validate_env_network_error_returns_warning_not_500(client: TestClient) -> None:
    with _mock_httpx_client(side_effect=httpx.ConnectError("connection refused")):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"GH_TOKEN": "ghp_sometoken"}},
        )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["is_token"] is True
    assert result["valid"] is False
    assert result["error"] is not None


# --------------------------------------------------------------------------
# Mixed keys — per-key results
# --------------------------------------------------------------------------


def test_validate_env_mixed_keys_returns_per_key_results(client: TestClient) -> None:
    with _mock_httpx_client(status_code=200, json_body={"login": "octocat"}):
        resp = client.post(
            "/projects/validate-env",
            json={"env_vars": {"GH_TOKEN": "ghp_valid", "WORKSPACE": "demo"}},
        )
    assert resp.status_code == 200
    results = {r["key"]: r for r in resp.json()["results"]}
    assert results["GH_TOKEN"]["is_token"] is True
    assert results["WORKSPACE"]["is_token"] is False


# --------------------------------------------------------------------------
# Schema rejects extra fields
# --------------------------------------------------------------------------


def test_validate_env_schema_rejects_extra_fields(client: TestClient) -> None:
    resp = client.post(
        "/projects/validate-env",
        json={"env_vars": {"X": "v"}, "bogus": 1},
    )
    assert resp.status_code == 422
