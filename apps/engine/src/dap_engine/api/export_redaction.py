"""Shared redaction helpers for portable export payloads."""

from __future__ import annotations

from typing import Any

_SECRET_KEY_EXACT = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
}
_SECRET_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_token",
    "_secret",
    "_password",
    "_credential",
)
_REDACTED_PLACEHOLDER = "<redacted>"


def _is_secret_like_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _SECRET_KEY_EXACT or normalized.endswith(_SECRET_KEY_SUFFIXES)


def scrub_secret_like_keys(value: dict[str, Any]) -> dict[str, Any]:
    """Best-effort redaction of keys whose name suggests a credential.

    Matches exact credential names and common suffixed forms such as
    ``access_token`` while preserving non-secret tuning keys such as
    ``max_tokens`` and ``tokenizer``.
    """
    redacted: dict[str, Any] = {}
    for key, val in value.items():
        if _is_secret_like_key(key):
            redacted[key] = _REDACTED_PLACEHOLDER
        elif isinstance(val, dict):
            redacted[key] = scrub_secret_like_keys(val)
        else:
            redacted[key] = val
    return redacted
