"""Smoke test — forgot/reset password flow (#300, sub-B3).

Covers the engine's ``/auth/forgot-password`` + ``/auth/reset-password``
endpoints exposed by ``fastapi_users.get_reset_password_router()``.

The ``on_after_forgot_password`` hook logs the token to engine output
at WARNING level (until email delivery lands in Phase E) — we capture
the log line from caplog and feed the captured token into the reset
endpoint so the round-trip is end-to-end.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

TEST_PASSWORD = "test-password-123"
NEW_PASSWORD = "new-test-password-456"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-reset-smoke-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-reset-secret-do-not-use-in-prod",
        # Opt-in to token logging for the test — the round-trip needs
        # to extract the token from caplog. Default is off in prod.
        auth_log_reset_tokens=True,
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, email: str) -> None:
    resp = client.post("/auth/register", json={"email": email, "password": TEST_PASSWORD})
    assert resp.status_code == 201, resp.text


def _extract_token(caplog: pytest.LogCaptureFixture) -> str:
    """Pull the reset token out of the ``on_after_forgot_password`` log line."""
    match = re.search(r"reset_token=(\S+)", caplog.text)
    assert match, f"No reset_token in log output:\n{caplog.text}"
    return match.group(1)


def test_forgot_password_returns_202_for_known_email(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Known email → 202, hook fires, token lands in the log."""
    _register(client, "alice@example.com")

    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 202, resp.text
    # Hook fired → token in log.
    assert "user.forgot_password" in caplog.text
    assert "reset_token=" in caplog.text


def test_forgot_password_returns_202_for_unknown_email(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown email also returns 202 — anti-enumeration. No token logged."""
    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        resp = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert resp.status_code == 202
    # No hook fires for a missing user, so no token is logged.
    assert "reset_token=" not in caplog.text


def test_reset_password_rotates_credentials(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """End-to-end: forgot → grab token from log → reset → new password works."""
    _register(client, "bob@example.com")

    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        forgot = client.post("/auth/forgot-password", json={"email": "bob@example.com"})
    assert forgot.status_code == 202
    token = _extract_token(caplog)

    # Use the token to rotate.
    reset = client.post(
        "/auth/reset-password",
        json={"token": token, "password": NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text

    # Old password no longer works.
    old_login = client.post(
        "/auth/jwt/login",
        data={"username": "bob@example.com", "password": TEST_PASSWORD},
    )
    assert old_login.status_code == 400

    # New password does.
    new_login = client.post(
        "/auth/jwt/login",
        data={"username": "bob@example.com", "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200


def test_reset_password_with_invalid_token_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/auth/reset-password",
        json={"token": "definitely-not-a-token", "password": NEW_PASSWORD},
    )
    assert resp.status_code == 400


def test_forgot_password_does_not_log_token_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default config (``auth_log_reset_tokens=False``) must NOT emit the
    raw reset token — reset tokens are credentials, and the default
    config is what production runs (Copilot review on PR #324).

    Captures at INFO so the "not logged" diagnostic is visible; we
    assert (a) the diagnostic line landed and (b) no record at any
    level carries ``reset_token=``.
    """
    with tempfile.TemporaryDirectory(prefix="dap-reset-default-") as tmp:
        config = EngineConfig(
            db_path=str(Path(tmp) / "state.db"),
            auth_jwt_secret="default-config-secret",
            # NOTE: auth_log_reset_tokens omitted → default False.
        )
        app = create_app(config)
        with TestClient(app) as c:
            c.post(
                "/auth/register",
                json={"email": "dee@example.com", "password": TEST_PASSWORD},
            )
            with caplog.at_level(logging.INFO, logger="dap.engine.auth"):
                resp = c.post("/auth/forgot-password", json={"email": "dee@example.com"})
            assert resp.status_code == 202
            assert "not logged" in caplog.text, caplog.text
            assert "reset_token=" not in caplog.text, caplog.text


def test_reset_password_with_short_password_returns_400(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The engine's ``min_length=8`` validator must run on reset too —
    otherwise a reset would bypass the same policy register enforces."""
    _register(client, "carol@example.com")
    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        client.post("/auth/forgot-password", json={"email": "carol@example.com"})
    token = _extract_token(caplog)
    resp = client.post("/auth/reset-password", json={"token": token, "password": "short"})
    assert resp.status_code == 400
