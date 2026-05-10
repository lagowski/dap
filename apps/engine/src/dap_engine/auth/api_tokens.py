"""API tokens for CLI / scripts (#299, sub-A3).

Tokens are opaque strings of the form ``dap_<43-char-base64>``:
- ``dap_`` prefix lets log filters / git-secret-scanners match the
  format unambiguously
- 32 bytes of OS randomness encoded as URL-safe base64 → 43 chars
  after the prefix (entropy 256 bits)
- The first 8 chars after the prefix become the indexed ``token_prefix``
  for fast DB lookup; the full token is hashed with SHA-256 (storing
  raw would defeat the point, but bcrypt-style KDFs are unnecessary
  here — see ``ApiTokenORM`` docstring for the calculus)

Verification:
- Authorization header ``Authorization: Bearer dap_*`` is the trigger
- DB lookup by prefix narrows the candidate set (almost always 1)
- ``hmac.compare_digest`` on the SHA-256 hash defeats timing attacks
- ``revoked_at`` non-NULL or ``expires_at`` past now → reject
- ``last_used_at`` is touched best-effort (separate UPDATE, no rollback
  on conflict — slightly stale ``last_used_at`` is acceptable, race
  conditions on the hot path are not worth a serialized transaction)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dap_engine.persistence.models import ApiTokenORM, UserORM

logger = logging.getLogger("dap.engine.auth.api_tokens")

TOKEN_PREFIX = "dap_"
TOKEN_RANDOM_BYTES = 32  # → 43 base64-url chars
TOKEN_INDEX_PREFIX_LEN = 8


class GeneratedToken(NamedTuple):
    """Returned by ``generate_token`` — caller stores prefix+hash, shows raw once."""

    raw: str
    """Full ``dap_<...>`` token. Show to the user once at create time."""

    prefix: str
    """First ``TOKEN_INDEX_PREFIX_LEN`` chars after ``dap_`` — DB indexed."""

    sha256_hash: str
    """Hex-encoded SHA-256 of ``raw``. DB stores this, not the raw value."""


def generate_token() -> GeneratedToken:
    """Generate a new opaque API token.

    Returns the raw form, the prefix used for indexed DB lookup, and
    the SHA-256 hash that the DB row should hold. The caller is
    expected to pair (prefix, hash) into an ``ApiTokenORM`` row, then
    return ``raw`` to the user — the token is irrecoverable afterwards.
    """
    random_part = secrets.token_urlsafe(TOKEN_RANDOM_BYTES)
    raw = f"{TOKEN_PREFIX}{random_part}"
    prefix = random_part[:TOKEN_INDEX_PREFIX_LEN]
    sha256_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return GeneratedToken(raw=raw, prefix=prefix, sha256_hash=sha256_hash)


def looks_like_api_token(value: str) -> bool:
    """True iff ``value`` has the API-token shape — used to route auth."""
    return value.startswith(TOKEN_PREFIX)


async def verify_api_token(  # noqa: PLR0911 — early-return matrix per failure mode keeps logic readable
    raw_token: str,
    session: AsyncSession,
) -> tuple[UserORM, ApiTokenORM] | None:
    """Look up + verify an API token. Returns ``(user, token)`` or ``None``.

    Touches ``last_used_at`` on success (best-effort; not transactional
    with the hit so a concurrent revoke / verify race won't roll back
    the actual auth result).
    """
    if not raw_token.startswith(TOKEN_PREFIX):
        return None
    random_part = raw_token[len(TOKEN_PREFIX) :]
    if len(random_part) < TOKEN_INDEX_PREFIX_LEN:
        return None

    prefix = random_part[:TOKEN_INDEX_PREFIX_LEN]
    candidate_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    # Lookup by indexed prefix; almost always 1 row, but iterate to
    # tolerate the (astronomically unlikely) collision case.
    stmt = select(ApiTokenORM).where(ApiTokenORM.token_prefix == prefix)
    rows = (await session.execute(stmt)).scalars().all()
    now = datetime.now(UTC)

    for token in rows:
        if not hmac.compare_digest(token.token_hash, candidate_hash):
            continue
        if token.revoked_at is not None:
            return None
        # SQLite strips tzinfo on roundtrip even with `DateTime(timezone=True)`,
        # so an expires_at read back is naive. Treat naive timestamps as UTC
        # — the writer always uses datetime.now(UTC) so this is safe.
        expires_at = token.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                return None
        # Found a live match — fetch the user.
        user_stmt = select(UserORM).where(UserORM.id == token.user_id)  # type: ignore[arg-type]
        user = (await session.execute(user_stmt)).scalars().unique().one_or_none()
        if user is None or not user.is_active:
            return None

        # Touch last_used_at — best-effort. Wrap in try/except so a
        # transient write failure (SQLite busy, lost FK to a
        # just-deleted user, etc.) doesn't promote a successful auth
        # to a 500. The token already passed all integrity checks
        # above; the only thing left is the audit-style timestamp,
        # which we'd rather lose than fail the request over.
        token.last_used_at = now
        try:
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            logger.warning(
                "auth.api_token.last_used_touch_failed",
                extra={"token_id": str(token.id), "user_id": str(user.id)},
                exc_info=True,
            )
        return user, token

    return None


def generate_token_uuid() -> uuid.UUID:
    """Convenience for callers that need a row id alongside the raw token."""
    return uuid.uuid4()
