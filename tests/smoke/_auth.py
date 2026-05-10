"""Test helpers for the auth-required engine (#299, sub-A4b2).

After Phase A's enforcement sub-PR every resource route requires a
valid bearer token. Smoke tests that exercise those routes need to
register and log in before they can do anything; this helper hides
the boilerplate behind a single context manager.

Usage:

    from tests.smoke._auth import authed_test_client

    @pytest.fixture
    def client() -> Iterator[TestClient]:
        tmp = tempfile.mkdtemp(prefix="dap-...")
        config = EngineConfig(
            db_path=str(Path(tmp) / "state.db"),
            auth_jwt_secret="test-secret",
        )
        app = create_app(config)
        with authed_test_client(app) as c:
            yield c

The helper registers a single ``test@local`` user, logs in to obtain a
JWT, and attaches it as the default ``Authorization: Bearer`` header on
the returned ``TestClient``. The user is *not* an admin — admin-only
tests should additionally set ``is_superuser=True`` directly via the
async session factory (see ``test_ownership.py`` for the pattern).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

# `test@local` rejects the email-validator's TLD check; use a fake-but-
# RFC-shaped domain so fastapi-users' EmailStr accepts it.
DEFAULT_TEST_EMAIL = "test@local.dev"
DEFAULT_TEST_PASSWORD = "test-password-123"


@contextmanager
def authed_test_client(
    app: FastAPI,
    *,
    email: str = DEFAULT_TEST_EMAIL,
    password: str = DEFAULT_TEST_PASSWORD,
) -> Iterator[TestClient]:
    """Yield a TestClient with a pre-registered user logged in via JWT.

    Lifespan boundaries are owned by the inner ``TestClient`` context.
    The bearer token is attached as a default header so test bodies
    don't have to thread it through every request.
    """
    with TestClient(app) as c:
        register = c.post("/auth/register", json={"email": email, "password": password})
        assert register.status_code == 201, register.text
        login = c.post(
            "/auth/jwt/login",
            data={"username": email, "password": password},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def register_and_login(
    client: TestClient, email: str, password: str = DEFAULT_TEST_PASSWORD
) -> str:
    """Register a second user and return their JWT (without modifying ``client``).

    Useful for cross-user ownership tests where one fixture-default
    user is already authenticated and the test needs a second identity
    to verify isolation.
    """
    register = client.post("/auth/register", json={"email": email, "password": password})
    assert register.status_code == 201, register.text
    login = client.post(
        "/auth/jwt/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]  # type: ignore[no-any-return]
