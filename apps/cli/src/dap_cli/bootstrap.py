"""Admin-user bootstrap for ``dap init`` (#302 sub-D4).

Creates (or promotes) an admin user on the engine's local SQLite
database without going through the HTTP API — the engine doesn't have
to be running. Re-uses ``fastapi-users``' password hasher (Argon2id
via pwdlib) so the resulting row is indistinguishable from one
created by ``/auth/register``.

Idempotent: if a user with the given email already exists, their
``is_superuser`` flag is set to ``True`` and the row is returned
unchanged otherwise.

The function writes ``.dap/bootstrap.json`` (chmod 600) with metadata
the operator (and ``dap status``) can inspect later — `email`,
`user_id`, `created_at`, `created_existing` flag.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Engine internals — same workspace, no public-API surface needed.
from dap_engine.app import EngineConfig
from dap_engine.persistence.db import create_engine_for_sqlite
from dap_engine.persistence.migrations import apply_migrations
from dap_engine.persistence.models import UserORM
from fastapi_users.password import PasswordHelper
from sqlalchemy import select
from sqlalchemy.orm import Session

# Engine-side password policy lives in ``UserManager.validate_password``;
# mirror it here so the CLI rejects bad passwords before they touch
# the DB. Keeping the value in lockstep with sub-A1's MIN_PASSWORD_LENGTH
# is a maintenance burden, but the engine module isn't importable
# without bootstrapping its DB — we accept the duplication for now.
MIN_PASSWORD_LENGTH = 8

# Generated random passwords. 22 base64url chars = 132 bits of entropy —
# plenty for any policy and short enough to read off a terminal.
GENERATED_PASSWORD_LENGTH = 22

# RFC 5322 covers an enormous surface; this regex catches typos
# (missing @, missing TLD) without claiming full RFC compliance. The
# engine's ``EmailStr`` validator catches the rest at register time
# if anyone gets through this guard.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """What ``ensure_admin_user`` did, surfaced to the caller for UI."""

    user_id: uuid.UUID
    email: str
    generated_password: str | None
    """The password the caller should print *once* (when ``dap init``
    generated it). ``None`` when the operator supplied one — we never
    echo a user-typed password."""
    promoted_existing: bool
    """True when the email already had a row and we flipped
    ``is_superuser`` to True. False when we inserted a fresh row."""


def validate_email(value: str) -> str:
    """Return a normalised email or raise ``ValueError``.

    Leading/trailing whitespace is stripped; the local part keeps
    case (RFC says it's case-sensitive, although every mail server
    in practice treats it case-insensitive). Email-validator
    boundary cases (IDN, comments) aren't supported — the engine's
    register endpoint has the full validator if needed.
    """
    candidate = value.strip()
    if not _EMAIL_RE.match(candidate):
        raise ValueError(f"not an email-looking value: {candidate!r}")
    return candidate


def validate_password(value: str) -> None:
    """Raise ``ValueError`` if the password violates the policy."""
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def generate_password() -> str:
    """Cryptographically-secure random password suitable for printing."""
    return secrets.token_urlsafe(GENERATED_PASSWORD_LENGTH)


def ensure_admin_user(
    db_path: Path,
    *,
    email: str,
    password: str | None,
) -> BootstrapResult:
    """Create or promote the admin user.

    ``password`` ``None`` → ``ensure_admin_user`` generates one. When
    the caller supplies a password, the function never returns it
    (so the calling code can't accidentally log the secret).

    Operates on the engine's local SQLite file directly. Migrations
    are applied first so a fresh ``.dap/state.db`` becomes
    structurally valid before the row write.
    """
    email = validate_email(email)
    generated: str | None = None
    if password is None or password == "":
        password = generate_password()
        generated = password
    validate_password(password)

    # Need a JWT secret on the EngineConfig to instantiate
    # ``create_engine_for_sqlite`` cleanly — the value doesn't matter
    # for the bootstrap operation (no token signing happens here),
    # but the engine refuses to construct an app without one. A
    # throwaway random keeps the helper standalone.
    EngineConfig(
        db_path=str(db_path),
        auth_jwt_secret=secrets.token_urlsafe(32),
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine_for_sqlite(str(db_path))
    apply_migrations(engine)

    hasher = PasswordHelper()
    hashed_password = hasher.hash(password)

    with Session(engine) as session:
        # ``.unique()`` is required because ``UserORM`` declares a
        # joined eager-load on ``oauth_identities`` (a collection) —
        # without de-duplication SQLAlchemy refuses to materialise a
        # single row from the multi-row JOIN result.
        existing = (
            session.scalars(
                select(UserORM).where(UserORM.email == email)  # type: ignore[arg-type]
            )
            .unique()
            .one_or_none()
        )
        if existing is not None:
            # ``promoted_existing`` means "we found an existing row and
            # left/forced it to admin", not "we flipped is_superuser
            # this call" — operators care about idempotency, not the
            # exact prior state. Keep this stable on re-runs.
            existing.is_superuser = True
            existing.is_active = True
            existing.deleted_at = None
            session.commit()
            return BootstrapResult(
                user_id=existing.id,
                email=email,
                generated_password=generated,
                promoted_existing=True,
            )

        now = _dt.datetime.now(_dt.UTC)
        user = UserORM(
            id=uuid.uuid4(),
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=True,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            last_login_at=None,
        )
        session.add(user)
        session.commit()
        return BootstrapResult(
            user_id=user.id,
            email=email,
            generated_password=generated,
            promoted_existing=False,
        )


def write_bootstrap_marker(path: Path, result: BootstrapResult) -> None:
    """Persist a small ``bootstrap.json`` next to ``state.db`` so
    ``dap status`` can show "admin user X created on Y".

    No secret material in the file — the generated password (when
    present) is only printed to stdout, never written.
    """
    payload = {
        "email": result.email,
        "user_id": str(result.user_id),
        "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "promoted_existing": result.promoted_existing,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # ``chmod 600`` — the file contains no secrets, but locking down
    # the permissions establishes the right reflex for any future
    # additions (e.g. recovery tokens). Best-effort on Windows where
    # POSIX modes don't apply.
    with contextlib.suppress(OSError, NotImplementedError):
        path.chmod(0o600)


def read_bootstrap_marker(path: Path) -> dict[str, Any] | None:
    """Read the bootstrap marker if it exists. ``None`` on missing /
    corrupt — caller treats either as "no bootstrap yet".

    Returns ``dict[str, Any]`` because the payload mixes strings
    (email, user_id, created_at) with booleans (``promoted_existing``).
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def bootstrap_marker_path(dap_dir: Path) -> Path:
    return dap_dir / "bootstrap.json"


__all__ = [
    "GENERATED_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "BootstrapResult",
    "bootstrap_marker_path",
    "ensure_admin_user",
    "generate_password",
    "read_bootstrap_marker",
    "validate_email",
    "validate_password",
    "write_bootstrap_marker",
]
