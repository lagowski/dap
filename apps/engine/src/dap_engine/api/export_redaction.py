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

# Recursion guard (#778 audit security #6): runtime_config is
# user-supplied JSON — a pathologically nested payload must not blow
# the interpreter recursion limit on export. Anything deeper than this
# is replaced wholesale; 32 levels is far beyond any legitimate config.
MAX_SCRUB_DEPTH = 32
_DEPTH_PLACEHOLDER = "<redacted:max-depth-exceeded>"


def _is_secret_like_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _SECRET_KEY_EXACT or normalized.endswith(_SECRET_KEY_SUFFIXES)


def scrub_secret_like_keys(value: dict[str, Any], *, _depth: int = 0) -> dict[str, Any]:
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
            redacted[key] = (
                _DEPTH_PLACEHOLDER
                if _depth + 1 >= MAX_SCRUB_DEPTH
                else scrub_secret_like_keys(val, _depth=_depth + 1)
            )
        else:
            redacted[key] = val
    return redacted
