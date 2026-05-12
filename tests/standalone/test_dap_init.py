"""End-to-end smoke for ``dap init`` admin bootstrap (#302 sub-D4).

These tests drive ``init_command`` in-process against a throwaway
``.dap/`` under ``tmp_path`` and verify three behaviours:

1. **Flag-driven happy path** — ``--admin-email`` + ``--admin-password``
   create a superuser whose credentials work against ``POST
   /auth/jwt/login`` on a freshly-spawned engine app.
2. **Idempotency** — re-running with the same email is safe; the
   existing user is promoted to ``is_superuser=True`` and the second
   call does not raise.
3. **Random-password generation** — empty interactive password (or
   non-interactive without ``--admin-password``) generates a password,
   exposes it on ``BootstrapResult.generated_password``, and the
   generated value logs in successfully.

We exercise ``init_command`` rather than spawning ``dap`` via
``subprocess`` so failures surface as Python tracebacks (much easier
to debug than CLI exit codes). The ``--admin-password-stdin`` path
*is* tested via subprocess because the function reads ``sys.stdin``
directly — easier to feed it bytes than to monkeypatch a global.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from dap_cli.bootstrap import (
    bootstrap_marker_path,
    ensure_admin_user,
    read_bootstrap_marker,
)
from dap_cli.commands.init import init_command
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

# A password long enough to satisfy both the CLI's minimum and any
# stricter engine-side rule that future migrations might add.
GOOD_PASSWORD = "hunter12345678"


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Treat ``tmp_path`` as the CWD so ``local_dap_dir()`` resolves there.

    ``dap_cli.paths.local_dap_dir()`` calls ``Path.cwd()`` — monkeypatch
    ``os.chdir`` once and the rest of the CLI machinery follows. The
    fixture restores the original directory on teardown via pytest's
    ``monkeypatch`` cleanup.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _try_login(db_path: Path, email: str, password: str) -> bool:
    """Spin up the engine app on the given SQLite file and POST to
    ``/auth/jwt/login``. Returns ``True`` on a 2xx; ``False`` otherwise.

    We deliberately avoid asserting on the response body — the only
    thing the test cares about is whether the credentials pass.
    """
    cfg = EngineConfig(
        db_path=str(db_path),
        auth_jwt_secret="test-secret-for-dap-init-smoke",
    )
    app = create_app(cfg)
    with TestClient(app) as client:
        response = client.post(
            "/auth/jwt/login",
            data={"username": email, "password": password},
        )
    return 200 <= response.status_code < 300


# --------------------------------------------------------------------- #
# 1. Happy path
# --------------------------------------------------------------------- #


def test_init_with_flags_creates_admin_who_can_log_in(
    project_dir: Path,
) -> None:
    """``dap init --admin-email=... --admin-password=...`` creates an
    admin row, writes the bootstrap marker, and the credentials work."""
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"

    init_command(
        admin_email=email,
        admin_password=GOOD_PASSWORD,
    )

    dap_dir = project_dir / ".dap"
    assert dap_dir.is_dir()
    assert (dap_dir / "config.json").is_file()

    marker = read_bootstrap_marker(bootstrap_marker_path(dap_dir))
    assert marker is not None
    assert marker["email"] == email
    assert marker["promoted_existing"] is False
    # uuid.UUID() raises on a non-UUID string — implicit format check.
    uuid.UUID(marker["user_id"])

    # The bootstrap file must be chmod 600 on POSIX systems. Skip the
    # check on Windows (sys.platform != darwin/linux) where Python's
    # ``Path.chmod`` is a no-op for read permission bits.
    if sys.platform in {"linux", "darwin"}:
        mode = (dap_dir / "bootstrap.json").stat().st_mode & 0o777
        assert mode == 0o600, f"bootstrap.json mode is {oct(mode)}, want 0o600"

    assert _try_login(dap_dir / "state.db", email, GOOD_PASSWORD), (
        "bootstrap admin failed to authenticate with the password we set"
    )


# --------------------------------------------------------------------- #
# 2. Idempotency / re-run
# --------------------------------------------------------------------- #


def test_init_rerun_promotes_existing_user(project_dir: Path) -> None:
    """Re-running ``init_command`` with ``--force`` against an existing
    user must not raise and must keep them logged-in-able."""
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"

    init_command(admin_email=email, admin_password=GOOD_PASSWORD)
    init_command(admin_email=email, admin_password=GOOD_PASSWORD, force=True)

    dap_dir = project_dir / ".dap"
    marker = read_bootstrap_marker(bootstrap_marker_path(dap_dir))
    assert marker is not None
    # The second call observed an existing row → ``promoted_existing``
    # should be True the second time. Our marker is overwritten, so we
    # assert against the final state.
    assert marker["email"] == email
    assert marker["promoted_existing"] is True

    assert _try_login(dap_dir / "state.db", email, GOOD_PASSWORD)


def test_init_rerun_with_new_password_resets_password(project_dir: Path) -> None:
    """When the operator re-runs ``dap init --force`` against an
    existing email AND passes a new ``--admin-password``, the new
    password must take effect (lost-admin-password recovery flow).

    Regression for the Copilot review on sub-E3 (#345): admin-guide.md
    documented this as the recovery procedure, but the bootstrap was
    only updating ``is_superuser`` / ``is_active`` and leaving the
    existing ``hashed_password`` intact — locking operators out of
    their own recovery path.
    """
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"
    initial_password = "initial-password-12345"
    new_password = "rotated-password-67890"

    # 1. First bootstrap with the initial password.
    init_command(admin_email=email, admin_password=initial_password)
    db_path = project_dir / ".dap" / "state.db"
    assert _try_login(db_path, email, initial_password)

    # 2. Operator forgets the password, re-runs with --force + new password.
    init_command(admin_email=email, admin_password=new_password, force=True)

    # 3. The new password works...
    assert _try_login(db_path, email, new_password), (
        "after rerun with new --admin-password, the new password should authenticate"
    )
    # ...and the old one doesn't (proves the rotation actually happened,
    # not just that the new password was added alongside).
    assert not _try_login(db_path, email, initial_password), (
        "old password should be invalidated after rerun"
    )


def test_init_rerun_without_password_preserves_existing(project_dir: Path) -> None:
    """Re-running ``dap init`` WITHOUT an explicit password (the
    auto-generate path) against an existing user must NOT silently
    rotate that user's credentials.

    Operators trip this when they re-run ``dap init`` to fix an
    unrelated setting (e.g. promote to admin) and would be unpleasantly
    surprised to find their existing login broken.
    """
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"
    explicit_password = "set-by-operator-12345"

    init_command(admin_email=email, admin_password=explicit_password)
    db_path = project_dir / ".dap" / "state.db"

    # Re-run WITHOUT --admin-password — bootstrap would normally
    # auto-generate one. For an EXISTING user, the auto-generated
    # value must not be applied.
    init_command(admin_email=email, admin_password=None, force=True)

    assert _try_login(db_path, email, explicit_password), (
        "auto-generated password should NOT have replaced the existing one"
    )


# --------------------------------------------------------------------- #
# 3. Random-password generation
# --------------------------------------------------------------------- #


def test_ensure_admin_user_generates_password_when_none_passed(
    tmp_path: Path,
) -> None:
    """``ensure_admin_user(..., password=None)`` must generate a
    password, return it once, and the generated value must work."""
    db_path = tmp_path / "state.db"
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"

    result = ensure_admin_user(db_path, email=email, password=None)

    assert result.generated_password is not None
    assert len(result.generated_password) >= 16  # token_urlsafe(22) ≈ 30 chars
    assert _try_login(db_path, email, result.generated_password)


# --------------------------------------------------------------------- #
# 4. stdin password (kubectl-style) — exercises the actual CLI binary
# --------------------------------------------------------------------- #


@pytest.mark.skipif(
    shutil.which("python3") is None,
    reason="subprocess test requires python3 on PATH",
)
def test_dap_init_reads_password_from_stdin(
    project_dir: Path,
) -> None:
    """Pipe a password via stdin and assert the resulting admin works.

    Runs ``python -m dap_cli`` rather than ``dap`` so the test works in
    a checkout without a pipx install; both paths exercise the same
    ``cmd_init`` typer entry point.
    """
    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"
    pw = "stdin-pipe-password-7777"

    # The env must include the venv's site-packages so the subprocess
    # can import dap_cli + dap_engine. pytest already exposes this via
    # the inherited PYTHONPATH; pass it through explicitly so the test
    # is robust to non-pytest invocation too.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "dap_cli",
            "init",
            "--admin-email",
            email,
            "--admin-password-stdin",
        ],
        input=pw,
        text=True,
        capture_output=True,
        cwd=project_dir,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, (
        f"dap init exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    assert _try_login(project_dir / ".dap" / "state.db", email, pw), (
        "stdin-supplied password didn't authenticate"
    )


# --------------------------------------------------------------------- #
# 5. ``dap status`` surfaces the bootstrap section
# --------------------------------------------------------------------- #


def test_status_shows_bootstrap_marker(
    project_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After ``init``, ``status_command`` must print the admin email
    line so operators can spot a bootstrapped instance at a glance."""
    from dap_cli.commands.status import status_command

    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"
    init_command(admin_email=email, admin_password=GOOD_PASSWORD)

    # Clear anything init might have printed.
    capsys.readouterr()

    status_command()
    out = capsys.readouterr().out
    assert "admin bootstrap" in out.lower()
    assert email in out


def test_status_hints_when_no_bootstrap(
    project_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When ``.dap/`` exists but ``bootstrap.json`` doesn't, status
    must nudge the operator to run ``dap init``."""
    from dap_cli.commands.status import status_command

    # Synthesise a half-initialised project: just the directory.
    (project_dir / ".dap").mkdir()

    status_command()
    out = capsys.readouterr().out
    # The exact wording can change — the test only locks the user-
    # facing affordance ("run dap init").
    assert "dap init" in out.lower()


# --------------------------------------------------------------------- #
# 6. DAP_DB_PATH env var override (#337 sub-D5 fix — Docker compose case)
# --------------------------------------------------------------------- #


def test_init_respects_dap_db_path_env_var(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dap init`` must write the admin row to the SQLite at
    ``$DAP_DB_PATH`` (not ``.dap/state.db``) so the engine inside a
    Docker container — which sets ``DAP_DB_PATH=/data/state.db`` —
    can authenticate against the same row.

    Regression for a Copilot-flagged bug in PR #342: the original D4
    bootstrap always landed in ``cwd/.dap/state.db``, leaving the
    engine looking at an empty ``/data/state.db``.
    """
    # Pick a non-default location outside the .dap/ skeleton. ``dap
    # init`` should write the admin row here AND the engine looking
    # at the same path should authenticate the supplied password.
    external_db = project_dir / "external" / "engine.db"
    monkeypatch.setenv("DAP_DB_PATH", str(external_db))

    email = f"admin-{uuid.uuid4().hex[:8]}@dap.local"
    init_command(admin_email=email, admin_password=GOOD_PASSWORD)

    assert external_db.is_file(), "dap init didn't write to $DAP_DB_PATH"
    assert _try_login(external_db, email, GOOD_PASSWORD), (
        "bootstrap admin missing from the env-var-pointed DB"
    )

    # And the in-CWD .dap/state.db should NOT have been created —
    # otherwise we've doubled the storage which is exactly the bug.
    default_db = project_dir / ".dap" / "state.db"
    assert not default_db.exists(), (
        "init wrote to BOTH the env-var path AND the default — the "
        "operator now has two databases to reconcile"
    )
