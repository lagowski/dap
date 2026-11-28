"""Smoke test — forgot/reset password flow (#300, sub-B3).

Covers the engine's ``/auth/forgot-password`` + ``/auth/reset-password``
endpoints exposed by ``fastapi_users.get_reset_password_router()``.

The ``on_after_forgot_password`` hook logs the token to engine output
at WARNING level (until email delivery lands in Phase E) — we capture
the log line from caplog and feed the captured token into the reset
endpoint so the round-trip is end-to-end.

Also (#audit E13) verifies that the two reset-flow hooks
(``on_after_forgot_password`` + ``on_after_reset_password``) write
the right audit rows with the right event_data — and that the
reset flow stays *distinct* from in-session password rotation
(``user.password_changed``, audited via PATCH /users/me; covered
by ``test_audit_log.py``). Conflating the two would erase the
threat-model distinction the event-type split was created for.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AuditLogORM
from fastapi.testclient import TestClient
from sqlalchemy import select

TEST_PASSWORD = "test-password-123"
NEW_PASSWORD = "new-test-password-456"


async def _audit_rows(client: TestClient, event_type: str | None = None) -> list[AuditLogORM]:
    """Read audit rows via the engine's async session factory.

    Mirrors the helper in ``test_audit_log.py``; duplicated here
    rather than imported so this file stays runnable in isolation
    (``pytest tests/smoke/test_auth_password_reset.py``).
    """
    factory = client.app.state.async_session_factory  # type: ignore[attr-defined]
    async with factory() as session:
        stmt = select(AuditLogORM).order_by(AuditLogORM.created_at)
        if event_type is not None:
            stmt = stmt.where(AuditLogORM.event_type == event_type)
        return list((await session.execute(stmt)).scalars().all())


@pytest.fixture
def client(
    engine_config_factory: Callable[..., EngineConfig],
) -> Iterator[TestClient]:
    """Custom client — needs ``auth_log_reset_tokens=True`` so the
    forgot-password hook surfaces the reset token via caplog (the only
    way to retrieve it without an email-delivery integration). All
    other test files use the shared conftest fixture; this one
    overrides locally because the flag is off by default in prod.
    """
    app = create_app(engine_config_factory(auth_log_reset_tokens=True))
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


# ─── Audit-event coverage (E13) ─────────────────────────────────────────
#
# The pre-E13 suite tested the HTTP shape of /auth/forgot-password +
# /auth/reset-password but never read the audit table back to confirm
# the hooks actually wrote rows. The risk this addresses: a future
# refactor of ``UserManager.on_after_forgot_password`` /
# ``on_after_reset_password`` could silently drop the audit-event
# emission (e.g. by routing through a different session that never
# commits) and every existing test would keep passing.


async def test_forgot_password_writes_audit_row(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """``on_after_forgot_password`` must emit ``user.forgot_password``
    with the requesting email and NO token in event_data — tokens are
    credentials, the audit table is read by admins through the UI."""
    _register(client, "eve@example.com")
    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        resp = client.post("/auth/forgot-password", json={"email": "eve@example.com"})
    assert resp.status_code == 202, resp.text

    rows = await _audit_rows(client, "user.forgot_password")
    assert len(rows) == 1
    row = rows[0]
    assert row.event_data is not None
    assert row.event_data["email"] == "eve@example.com"
    # Token must never end up in event_data even when the diagnostic
    # log carries it — different surfaces, different threat models.
    serialised = repr(row.event_data)
    assert "reset_token" not in serialised
    assert "token" not in row.event_data


async def test_forgot_password_unknown_email_writes_no_audit_row(
    client: TestClient,
) -> None:
    """Anti-enumeration policy: unknown email returns 202 but must NOT
    write an audit row. Otherwise an attacker with read access could
    enumerate registered accounts by triggering forgot-password against
    candidate emails and watching the audit table grow."""
    resp = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert resp.status_code == 202

    rows = await _audit_rows(client, "user.forgot_password")
    assert rows == []


async def test_reset_password_writes_audit_row(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """End-to-end forgot → reset must land *one* ``user.password_reset``
    audit row tagged with the user's email. The corresponding
    ``user.password_changed`` event_type must NOT appear — that one is
    reserved for in-session PATCH /users/me rotations (different threat
    model, different incident-response handling).
    """
    _register(client, "frank@example.com")
    with caplog.at_level(logging.WARNING, logger="dap.engine.auth"):
        client.post("/auth/forgot-password", json={"email": "frank@example.com"})
    token = _extract_token(caplog)

    reset = client.post(
        "/auth/reset-password",
        json={"token": token, "password": NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text

    reset_rows = await _audit_rows(client, "user.password_reset")
    assert len(reset_rows) == 1
    row = reset_rows[0]
    assert row.event_data is not None
    assert row.event_data["email"] == "frank@example.com"
    # New password must never end up in event_data — defence in depth
    # against the manager hook accidentally splatting update_dict in.
    assert "password" not in row.event_data
    assert NEW_PASSWORD not in repr(row.event_data)

    # Distinctness: the reset flow MUST NOT emit user.password_changed.
    # That's the PATCH /users/me path and it has a different
    # threat-model (active session) + audit-investigation playbook.
    changed_rows = await _audit_rows(client, "user.password_changed")
    assert changed_rows == [], "user.password_reset and user.password_changed must stay distinct"
