"""At-rest encryption for instance-level secrets (#388).

Used to encrypt instance env-var values stored in the ``instance_env_vars``
table. The plaintext never lives on disk; only the Fernet ciphertext does.

Key bootstrap (``DAP_INSTANCE_ENV_VARS_KEY``):
    A 32-byte url-safe base64 Fernet key. Unlike the JWT secret — where
    a per-process random is fine because tokens are short-lived — this
    key MUST persist across restarts: encrypted DB rows written by an
    earlier process must decrypt against the same key today. We refuse
    to start when an admin route is needed but the key is unset (the
    lifespan crashes loudly rather than silently producing rows we
    can't read back).

    Generate one with: ``python -c 'from cryptography.fernet import
    Fernet; print(Fernet.generate_key().decode())'``.

Why Fernet (not raw AES-GCM):
    Fernet bundles authentication, IV management and key rotation in
    a stable wire format — leaning on a well-reviewed primitive is
    safer than rolling AES-GCM by hand for a feature that touches
    customer secrets.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["EncryptionError", "decrypt_value", "encrypt_value"]


class EncryptionError(RuntimeError):
    """Raised when the configured key cannot be parsed, or when a row's
    ciphertext fails to decrypt (key rotation gone wrong, DB tampering,
    or a row written with a different key)."""


def _build_fernet(key: str) -> Fernet:
    """Parse the key once per call — Fernet itself caches nothing
    expensive, and rebuilding on every encrypt/decrypt keeps the call
    sites stateless (no module-level singleton that would need
    invalidation on key rotation in tests)."""
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise EncryptionError(
            "DAP_INSTANCE_ENV_VARS_KEY is not a valid Fernet key — generate one "
            "with `python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`."
        ) from exc


def encrypt_value(plaintext: str, *, key: str) -> str:
    """Return a Fernet ciphertext (url-safe base64) suitable for a
    TEXT column. Empty plaintext is intentionally rejected: the storage
    layer never holds empty values (the API layer also enforces this),
    so silently accepting one here would mask a contract violation."""
    if not plaintext:
        raise EncryptionError("Refusing to encrypt empty plaintext")
    fernet = _build_fernet(key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_value(ciphertext: str, *, key: str) -> str:
    """Inverse of ``encrypt_value``. Raises ``EncryptionError`` if the
    row was written with a different key (typical cause: operator
    rotated the key without re-encrypting the rows). Callers should
    treat that as fatal rather than dropping the row silently."""
    fernet = _build_fernet(key)
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError(
            "Failed to decrypt instance env-var value — the row was likely "
            "written with a different DAP_INSTANCE_ENV_VARS_KEY."
        ) from exc
