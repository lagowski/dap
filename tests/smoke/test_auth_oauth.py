"""Smoke test — OAuth provider wiring (#299, sub-A2).

Verifies that GitHub + Google OAuth providers are correctly mounted
when credentials are configured and absent when they aren't. The
end-to-end callback flow (state token validation, code exchange,
user-account linking) is covered by fastapi-users' own upstream test
suite; replicating it here would mean reverse-engineering its
internal state-token signing — brittle and high-noise.

What this covers:
- ``/auth/github/*`` and ``/auth/google/*`` routes exist when both
  client_id and client_secret are configured for the provider
- The same routes are absent (404) when either credential is unset —
  partial config doesn't half-mount a router
- ``/auth/<provider>/authorize`` returns a real provider authorization
  URL with our ``client_id``, callback URL, and signed state token
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def oauth_client() -> Iterator[TestClient]:
    """TestClient with both GitHub + Google OAuth providers wired."""
    tmp = tempfile.mkdtemp(prefix="dap-smoke-oauth-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="oauth-smoke-secret",
        oauth_github_client_id="gh-test-id",
        oauth_github_client_secret="gh-test-secret",
        oauth_google_client_id="google-test-id",
        oauth_google_client_secret="google-test-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def password_only_client() -> Iterator[TestClient]:
    """TestClient with NO OAuth providers wired (default config)."""
    tmp = tempfile.mkdtemp(prefix="dap-smoke-pwonly-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="oauth-smoke-secret",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_oauth_routes_only_mounted_when_credentials_present(
    password_only_client: TestClient,
) -> None:
    """Provider routes must NOT exist when credentials are unset.

    A request to ``/auth/github/authorize`` on a client built without
    GitHub credentials should return 404 — not a misleading 500 from a
    half-mounted router with missing config.
    """
    resp = password_only_client.get("/auth/github/authorize")
    assert resp.status_code == 404
    resp = password_only_client.get("/auth/google/authorize")
    assert resp.status_code == 404


def test_oauth_routes_present_when_configured(oauth_client: TestClient) -> None:
    paths = {r.path for r in oauth_client.app.routes}  # type: ignore[attr-defined]
    assert "/auth/github/authorize" in paths
    assert "/auth/github/callback" in paths
    assert "/auth/google/authorize" in paths
    assert "/auth/google/callback" in paths


def test_github_authorize_returns_authorization_url(oauth_client: TestClient) -> None:
    """The /authorize endpoint hands back a real GitHub authorize URL.

    fastapi-users delegates this to ``httpx_oauth.OAuth2.get_authorization_url``;
    we don't intercept it because the upstream implementation is a pure
    string-builder that doesn't make network calls.
    """
    resp = oauth_client.get("/auth/github/authorize")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "authorization_url" in payload
    assert payload["authorization_url"].startswith("https://github.com/login/oauth/authorize")
    # The state token is generated server-side; just check it's wired in.
    assert "state=" in payload["authorization_url"]


def test_google_authorize_returns_authorization_url(oauth_client: TestClient) -> None:
    """Same shape as the GitHub test — confirms Google is also wired up."""
    resp = oauth_client.get("/auth/google/authorize")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "authorization_url" in payload
    assert payload["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "state=" in payload["authorization_url"]
    # client_id from the fixture must appear in the URL.
    assert "client_id=google-test-id" in payload["authorization_url"]


def test_authorize_url_includes_dashboard_redirect_when_configured(
    tmp_path: Path,
) -> None:
    """``auth_oauth_redirect_url`` lands as the ``redirect_uri`` in the
    provider authorize URL — the dashboard callback handler in B5 reads
    the resulting ``?token=`` and promotes it to a cookie. Without this
    plumbing the engine would default to its own callback URL and the
    browser would end up staring at raw JSON."""
    from urllib.parse import parse_qs, urlparse

    cfg = EngineConfig(
        db_path=str(tmp_path / "state.db"),
        auth_jwt_secret="oauth-smoke-secret",
        oauth_github_client_id="gh-id",
        oauth_github_client_secret="gh-secret",
        auth_oauth_redirect_url="http://localhost:3000/api/auth/oauth/callback",
    )
    app = create_app(cfg)
    with TestClient(app) as c:
        resp = c.get("/auth/github/authorize")
        assert resp.status_code == 200, resp.text
        params = parse_qs(urlparse(resp.json()["authorization_url"]).query)
        assert params["redirect_uri"] == ["http://localhost:3000/api/auth/oauth/callback"]


def test_partial_credentials_do_not_mount_router(tmp_path: Path) -> None:
    """If client_id is set but secret is missing (or vice-versa), no route.

    Helps operators diagnose half-configured OAuth: the provider simply
    doesn't appear, so the failure mode is "404 no such endpoint" rather
    than a confusing 500 from inside an unconfigured router.
    """
    cfg = EngineConfig(
        db_path=str(tmp_path / "state.db"),
        auth_jwt_secret="oauth-smoke-secret",
        oauth_github_client_id="set-but-no-secret",
        oauth_github_client_secret=None,
    )
    app = create_app(cfg)
    with TestClient(app) as c:
        assert c.get("/auth/github/authorize").status_code == 404
