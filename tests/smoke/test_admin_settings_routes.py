"""Smoke test — admin instance-settings endpoint (#301, sub-C5, closes #331).

Covers ``GET /settings/admin``:
- Auth gate: 401 anonymous, 404 non-admin, 200 admin.
- Field shape matches the dashboard's expectations.
- Secrets are NEVER in the response — only presence flags.
- Database URL credentials are redacted on the PostgreSQL path.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.db import redact_database_url
from dap_engine.persistence.models import UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-admin-settings-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="admin-settings-smoke-secret",
        oauth_github_client_id="gh-id",
        oauth_github_client_secret="gh-secret",
        # Google left unset so we can verify the "not configured" path
        # at the same time.
        auth_oauth_redirect_url="http://localhost:3000/api/auth/oauth/callback",
        cors_origins=["http://localhost:3000"],
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


def test_admin_settings_returns_401_for_anonymous(client: TestClient) -> None:
    assert client.get("/settings/admin").status_code == 401


def test_admin_settings_returns_404_for_non_admin(client: TestClient) -> None:
    """Anti-enumeration: non-admin must not learn the endpoint exists."""
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.get("/settings/admin", headers=_bearer(token))
    assert resp.status_code == 404


def test_admin_settings_returns_full_snapshot_for_admin(client: TestClient) -> None:
    """Happy path — every field group landed, presence flags match config."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get("/settings/admin", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level groups.
    assert set(body.keys()) == {"auth", "oauth", "cors", "storage"}

    # Auth: secret flagged as configured (we set it in the fixture).
    assert body["auth"]["jwt_secret_configured"] is True
    assert body["auth"]["access_ttl_seconds"] == 60 * 15  # default
    assert body["auth"]["log_reset_tokens"] is False

    # OAuth: GitHub configured in the fixture, Google not.
    assert body["oauth"]["github"]["configured"] is True
    assert body["oauth"]["google"]["configured"] is False
    assert body["oauth"]["google"]["client_id_configured"] is False
    assert body["oauth"]["redirect_url"] == "http://localhost:3000/api/auth/oauth/callback"

    # CORS: explicit list from the fixture → using_default=False.
    assert body["cors"]["origins"] == ["http://localhost:3000"]
    assert body["cors"]["using_default"] is False

    # Storage: SQLite path (default backend in tests).
    assert body["storage"]["backend"] == "sqlite"
    assert body["storage"]["location"].endswith("state.db")


def test_admin_settings_cors_using_default_when_unset(client: TestClient) -> None:
    """When ``cors_origins`` is ``None`` the engine falls back to
    ``DEFAULT_CORS_ORIGINS`` — the endpoint must surface the effective
    list (not ``None``) plus a ``using_default=True`` flag so the
    dashboard can chip it as "from defaults" (Copilot review on PR
    #332). Otherwise operators reading the page would think they have
    a permissive CORS policy when they're actually on the local-dev
    allow-list."""
    with tempfile.TemporaryDirectory(prefix="dap-cors-default-") as tmp:
        # ``cors_origins`` omitted → defaults to None on EngineConfig.
        config = EngineConfig(
            db_path=str(Path(tmp) / "state.db"),
            auth_jwt_secret="cors-default-secret",
        )
        app = create_app(config)
        with TestClient(app) as c:
            c.post(
                "/auth/register",
                json={"email": "alice@example.com", "password": PASSWORD},
            )
            _promote_to_admin(app, "alice@example.com")
            login = c.post(
                "/auth/jwt/login",
                data={"username": "alice@example.com", "password": PASSWORD},
            )
            token = login.json()["access_token"]
            resp = c.get("/settings/admin", headers=_bearer(token))
            assert resp.status_code == 200
            cors = resp.json()["cors"]
            assert cors["using_default"] is True
            # The effective list must NOT be empty — that would imply
            # an explicit "deny all", which isn't what ``None`` means.
            assert len(cors["origins"]) > 0


def test_admin_settings_storage_detects_sqlite_url(client: TestClient) -> None:
    """``database_url=sqlite://...`` must report ``backend=sqlite``,
    not ``postgresql``. Earlier sub-C5 versions treated any non-None
    URL as Postgres (Copilot review on PR #332); this asserts the
    fix using the same ``detect_dialect`` helper ``create_app`` uses."""
    with tempfile.TemporaryDirectory(prefix="dap-sqlite-url-") as tmp:
        # SQLAlchemy needs an absolute path or ``:memory:`` for sqlite URLs.
        db_path = Path(tmp) / "state.db"
        config = EngineConfig(
            db_path=str(db_path),
            database_url=f"sqlite:///{db_path}",
            auth_jwt_secret="sqlite-url-secret",
        )
        app = create_app(config)
        with TestClient(app) as c:
            c.post(
                "/auth/register",
                json={"email": "alice@example.com", "password": PASSWORD},
            )
            _promote_to_admin(app, "alice@example.com")
            login = c.post(
                "/auth/jwt/login",
                data={"username": "alice@example.com", "password": PASSWORD},
            )
            token = login.json()["access_token"]
            resp = c.get("/settings/admin", headers=_bearer(token))
            assert resp.status_code == 200, resp.text
            storage = resp.json()["storage"]
            assert storage["backend"] == "sqlite"


def test_admin_settings_never_returns_raw_secrets(client: TestClient) -> None:
    """Belt-and-braces check: the response body MUST NOT contain the
    raw JWT secret or any OAuth client secret value."""
    _register(client, "alice@example.com")
    _promote_to_admin(client.app, "alice@example.com")
    token = _login(client, "alice@example.com")

    resp = client.get("/settings/admin", headers=_bearer(token))
    assert resp.status_code == 200
    body_text = resp.text
    assert "admin-settings-smoke-secret" not in body_text
    assert "gh-secret" not in body_text


def test_redact_database_url_masks_password() -> None:
    """Unit-level — feed common shapes and assert credentials disappear.

    Uses SQLAlchemy's URL parser (``make_url`` + ``render_as_string``)
    so URL-encoded passwords + IPv6 hosts + query params round-trip
    safely (Copilot review on PR #332).
    """
    redacted = redact_database_url(
        "postgresql+psycopg://app_user:hunter2@db.internal:5432/dap",
    )
    assert "hunter2" not in redacted
    assert "app_user" in redacted  # user keeps visibility
    assert "db.internal:5432/dap" in redacted

    # URL-encoded password with ``@`` inside it must NOT leak.
    encoded = redact_database_url(
        "postgresql+psycopg://app_user:p%40ss%40word@db.internal:5432/dap",
    )
    assert "p%40ss%40word" not in encoded
    assert "p@ss@word" not in encoded

    # No credentials → still parseable, no spurious changes.
    plain = "postgresql://db.internal:5432/dap"
    assert "db.internal:5432" in redact_database_url(plain)
