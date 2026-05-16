"""Smoke tests for admin instance-env-vars endpoints (#388).

Covers ``GET /settings/admin/env-vars``, ``POST /settings/admin/env-vars``
and ``DELETE /settings/admin/env-vars/{key}``:

- Auth gate: 401 anonymous, 404 non-admin (anti-enumeration), 200/204 admin.
- Values are encrypted at rest and NEVER returned raw — GET surfaces a
  short preview only.
- Key names validated against ``^[A-Z_][A-Z0-9_]*$`` and a denylist of
  reserved prefixes (``DAP_``, ``POSTGRES_``, ``PYTHON*``) plus a few
  exact engine-bootstrap keys (``DATABASE_URL``, ``PATH``,
  ``LD_LIBRARY_PATH``, ``LD_PRELOAD``).
- Audit log records ``settings.env_var.{created,updated,deleted}`` —
  ``event_data`` MUST NOT contain the secret value.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AuditLogORM, UserORM
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

PASSWORD = "test-password-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-admin-env-vars-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="admin-env-vars-smoke-secret",
        # Fernet key for at-rest encryption of instance env-var values.
        # A freshly generated key per test fixture means the encrypted
        # blob in the DB is unreadable across test runs — perfect
        # isolation, no leakage.
        instance_env_vars_key=Fernet.generate_key().decode(),
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


def _admin_client(client: TestClient) -> dict[str, str]:
    """Register, promote to admin, login — return Bearer header."""
    _register(client, "admin@example.com")
    _promote_to_admin(client.app, "admin@example.com")
    return _bearer(_login(client, "admin@example.com"))


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_get_env_vars_returns_401_for_anonymous(client: TestClient) -> None:
    assert client.get("/settings/admin/env-vars").status_code == 401


def test_get_env_vars_returns_404_for_non_admin(client: TestClient) -> None:
    """Anti-enumeration: non-admin must not learn the endpoint exists."""
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.get("/settings/admin/env-vars", headers=_bearer(token))
    assert resp.status_code == 404


def test_post_env_vars_returns_401_for_anonymous(client: TestClient) -> None:
    assert (
        client.post("/settings/admin/env-vars", json={"GH_TOKEN_CODE": "ghp_x"}).status_code == 401
    )


def test_post_env_vars_returns_404_for_non_admin(client: TestClient) -> None:
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_x"},
        headers=_bearer(token),
    )
    assert resp.status_code == 404


def test_delete_env_var_returns_404_for_non_admin(client: TestClient) -> None:
    _register(client, "alice@example.com")
    token = _login(client, "alice@example.com")
    resp = client.delete("/settings/admin/env-vars/GH_TOKEN_CODE", headers=_bearer(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


def test_get_env_vars_empty_initially(client: TestClient) -> None:
    headers = _admin_client(client)
    resp = client.get("/settings/admin/env-vars", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"env_vars": []}


def test_post_creates_var_then_get_returns_it_masked(client: TestClient) -> None:
    headers = _admin_client(client)

    create = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_supersecretvalue1234"},
        headers=headers,
    )
    assert create.status_code == 200, create.text

    resp = client.get("/settings/admin/env-vars", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["env_vars"] == [
        {"key": "GH_TOKEN_CODE", "preview": "ghp_••••", "value_set": True},
    ]


def test_post_bulk_upsert_uses_single_select(client: TestClient) -> None:
    """Bulk POST must SELECT existing rows in one query, not N+1 (Copilot
    review on PR #435). Patch ``Session.execute`` to count SELECTs against
    ``instance_env_vars`` during a 5-key bulk POST and assert the lookup
    cost is constant in the batch size."""
    from unittest.mock import patch

    headers = _admin_client(client)
    # Seed one existing row so the upsert exercises BOTH the create and
    # update branches in a single batch (the N+1 fix has to apply to
    # both).
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_EXISTING": "ghp_seed_xxxxxxx"},
        headers=headers,
    )

    original_execute = Session.execute
    select_count = {"n": 0}

    def counting_execute(self: Session, statement: Any, *args: Any, **kwargs: Any) -> Any:
        # Compile to SQL text once per call to inspect the table name —
        # cheap (no DB round-trip).
        try:
            compiled = str(statement.compile(compile_kwargs={"literal_binds": False})).lower()
        except Exception:
            compiled = ""
        if compiled.startswith("select") and "instance_env_vars" in compiled:
            select_count["n"] += 1
        return original_execute(self, statement, *args, **kwargs)

    with patch.object(Session, "execute", counting_execute):
        resp = client.post(
            "/settings/admin/env-vars",
            json={
                "GH_TOKEN_A": "ghp_a_xxxxxxx",
                "GH_TOKEN_B": "ghp_b_xxxxxxx",
                "GH_TOKEN_C": "ghp_c_xxxxxxx",
                "GH_TOKEN_EXISTING": "ghp_updated_xxxxxxx",
                "GH_TOKEN_D": "ghp_d_xxxxxxx",
            },
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    # Upsert path issues exactly two ``instance_env_vars`` SELECTs:
    # one bulk lookup of existing rows + one final listing read.
    # Anything > 2 means an N+1 has crept back in.
    assert select_count["n"] == 2, (
        f"expected 2 SELECTs against instance_env_vars (bulk lookup + final list), "
        f"got {select_count['n']} — N+1 regression"
    )


def test_post_multiple_keys_in_single_request(client: TestClient) -> None:
    headers = _admin_client(client)
    resp = client.post(
        "/settings/admin/env-vars",
        json={
            "GH_TOKEN_CODE": "ghp_aaaaaaaaaaaa",
            "GH_TOKEN_MERGE": "ghp_bbbbbbbbbbbb",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    listing = client.get("/settings/admin/env-vars", headers=headers).json()
    keys = sorted(row["key"] for row in listing["env_vars"])
    assert keys == ["GH_TOKEN_CODE", "GH_TOKEN_MERGE"]


def test_post_upserts_existing_key(client: TestClient) -> None:
    headers = _admin_client(client)
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_old_value_xxxx"},
        headers=headers,
    )
    update = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_new_value_yyyy"},
        headers=headers,
    )
    assert update.status_code == 200

    listing = client.get("/settings/admin/env-vars", headers=headers).json()
    rows = [r for r in listing["env_vars"] if r["key"] == "GH_TOKEN_CODE"]
    assert len(rows) == 1
    # Preview reflects the *new* value, not the old one.
    assert rows[0]["preview"].startswith("ghp_")


def test_delete_removes_var(client: TestClient) -> None:
    headers = _admin_client(client)
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_value_to_delete"},
        headers=headers,
    )

    delete = client.delete("/settings/admin/env-vars/GH_TOKEN_CODE", headers=headers)
    assert delete.status_code == 204

    listing = client.get("/settings/admin/env-vars", headers=headers).json()
    assert listing == {"env_vars": []}


def test_delete_unknown_key_returns_404(client: TestClient) -> None:
    headers = _admin_client(client)
    resp = client.delete("/settings/admin/env-vars/NONEXISTENT_KEY", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Secrets never leak
# ---------------------------------------------------------------------------


def test_get_never_returns_raw_value(client: TestClient) -> None:
    """Belt-and-braces: the raw value MUST NOT appear in the response body."""
    headers = _admin_client(client)
    raw = "ghp_thisisthefullsecretvalue_donotleakit_1234567890"
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": raw},
        headers=headers,
    )
    resp = client.get("/settings/admin/env-vars", headers=headers)
    assert resp.status_code == 200
    assert raw not in resp.text


def test_post_response_never_returns_raw_value(client: TestClient) -> None:
    """The POST response itself must not echo the value back."""
    headers = _admin_client(client)
    raw = "ghp_echoback_check_value_098765"
    resp = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": raw},
        headers=headers,
    )
    assert resp.status_code == 200
    assert raw not in resp.text


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "lower_case",  # lowercase not allowed
        "MIXED_Case",  # mixed case not allowed
        "WITH-DASH",  # dashes not allowed
        "WITH SPACE",  # spaces not allowed
        "123_LEADING_DIGIT",  # cannot start with digit
        "",  # empty
        "WITH.DOT",  # dots not allowed
    ],
)
def test_post_rejects_invalid_key_pattern(client: TestClient, bad_key: str) -> None:
    headers = _admin_client(client)
    resp = client.post(
        "/settings/admin/env-vars",
        json={bad_key: "value"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "reserved_key",
    [
        "DAP_AUTH_JWT_SECRET",  # DAP_ prefix reserved for engine config
        "DAP_DATABASE_URL",
        "POSTGRES_PASSWORD",  # POSTGRES_ prefix reserved
        "PYTHONPATH",  # PYTHON* reserved
        "PYTHONHOME",
        "DATABASE_URL",  # exact denylist
        "PATH",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
    ],
)
def test_post_rejects_reserved_keys(client: TestClient, reserved_key: str) -> None:
    """Refuse keys that could break the engine or leak through to subprocesses
    with privileges (LD_PRELOAD / LD_LIBRARY_PATH) — operator can still set
    these via the OS env, but not as instance overrides."""
    headers = _admin_client(client)
    resp = client.post(
        "/settings/admin/env-vars",
        json={reserved_key: "value"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_post_rejects_non_string_value(client: TestClient) -> None:
    headers = _admin_client(client)
    resp = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": 12345},
        headers=headers,
    )
    assert resp.status_code == 422


def test_post_rejects_empty_value(client: TestClient) -> None:
    """Empty value would shadow engine env without an obvious reason —
    use DELETE to remove a var, not POST with empty string."""
    headers = _admin_client(client)
    resp = client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": ""},
        headers=headers,
    )
    assert resp.status_code == 422


def test_post_rejects_empty_body(client: TestClient) -> None:
    headers = _admin_client(client)
    resp = client.post("/settings/admin/env-vars", json={}, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _read_audit_events(app: Any, event_type: str) -> list[dict[str, Any]]:
    async def _do() -> list[dict[str, Any]]:
        async with app.state.async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLogORM).where(AuditLogORM.event_type == event_type)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {"event_type": r.event_type, "event_data": r.event_data, "user_id": r.user_id}
                for r in rows
            ]

    return asyncio.run(_do())


def test_audit_event_recorded_on_create(client: TestClient) -> None:
    headers = _admin_client(client)
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_audit_create_test"},
        headers=headers,
    )
    events = _read_audit_events(client.app, "settings.env_var.created")
    assert len(events) == 1
    assert events[0]["event_data"] == {"key": "GH_TOKEN_CODE"}


def test_audit_event_recorded_on_update(client: TestClient) -> None:
    headers = _admin_client(client)
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_initial"},
        headers=headers,
    )
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_updated"},
        headers=headers,
    )
    updated = _read_audit_events(client.app, "settings.env_var.updated")
    assert len(updated) == 1
    assert updated[0]["event_data"] == {"key": "GH_TOKEN_CODE"}


def test_audit_event_recorded_on_delete(client: TestClient) -> None:
    headers = _admin_client(client)
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": "ghp_to_delete"},
        headers=headers,
    )
    client.delete("/settings/admin/env-vars/GH_TOKEN_CODE", headers=headers)
    deleted = _read_audit_events(client.app, "settings.env_var.deleted")
    assert len(deleted) == 1
    assert deleted[0]["event_data"] == {"key": "GH_TOKEN_CODE"}


def test_audit_event_never_contains_value(client: TestClient) -> None:
    """The audit-log JSONB column is admin-visible; the secret value
    must never appear in it."""
    headers = _admin_client(client)
    raw = "ghp_should_never_appear_in_audit_log_xyz"
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": raw},
        headers=headers,
    )
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": raw + "v2"},
        headers=headers,
    )
    client.delete("/settings/admin/env-vars/GH_TOKEN_CODE", headers=headers)

    for event_type in (
        "settings.env_var.created",
        "settings.env_var.updated",
        "settings.env_var.deleted",
    ):
        events = _read_audit_events(client.app, event_type)
        for event in events:
            payload = event["event_data"] or {}
            for value in payload.values():
                assert raw not in str(value), f"audit event {event_type} leaked value: {payload!r}"


# ---------------------------------------------------------------------------
# At-rest encryption: ciphertext in DB is unreadable without the Fernet key
# ---------------------------------------------------------------------------


def test_db_storage_is_encrypted_at_rest(client: TestClient) -> None:
    """A plain SQL select must NOT find the raw value — only ciphertext."""
    headers = _admin_client(client)
    raw = "ghp_check_encryption_at_rest_value_xyz"
    client.post(
        "/settings/admin/env-vars",
        json={"GH_TOKEN_CODE": raw},
        headers=headers,
    )

    app: Any = client.app

    async def _scan() -> str:
        from sqlalchemy import text

        async with app.state.async_session_factory() as session:
            rows = (await session.execute(text("SELECT * FROM instance_env_vars"))).fetchall()
            return "\n".join(str(r) for r in rows)

    dumped = asyncio.run(_scan())
    assert raw not in dumped
