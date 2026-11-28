"""Unit tests for CORS origin parsing/validation (#778 audit security #5).

``parse_cors_origins`` previously accepted any comma-separated junk —
a typo'd ``DAP_CORS_ORIGINS`` (missing scheme, stray ``*``) silently
produced origins the browser would never match. Malformed entries now
fail engine startup loudly, consistent with the DAP_LOG_LEVEL policy.
"""

from __future__ import annotations

import pytest
from dap_engine.config import parse_cors_origins


def test_none_and_blank_fall_back_to_default() -> None:
    assert parse_cors_origins(None) is None
    assert parse_cors_origins("") is None
    assert parse_cors_origins("  ,  ") is None


def test_parses_comma_separated_origins() -> None:
    assert parse_cors_origins("https://a.example, http://b.local:3000") == [
        "https://a.example",
        "http://b.local:3000",
    ]


def test_wildcard_is_allowed_as_explicit_operator_choice() -> None:
    assert parse_cors_origins("*") == ["*"]


@pytest.mark.parametrize(
    "raw",
    [
        "a.example",  # missing scheme — browser would never match
        "ftp://a.example",  # non-http scheme
        "https://",  # scheme without host
        # Origin headers never carry a path/trailing slash — an entry
        # with one would silently never match in CORSMiddleware's exact
        # string comparison (PR #783 council review).
        "https://a.example/",
        "https://a.example/path",
    ],
)
def test_rejects_malformed_origin_entries(raw: str) -> None:
    with pytest.raises(ValueError, match="DAP_CORS_ORIGINS"):
        parse_cors_origins(raw)


# ---------------------------------------------------------------------------
# Retention-days env parsing (#722, PR #784 council review)
# ---------------------------------------------------------------------------


def test_parse_retention_days_defaults_and_parses() -> None:
    from dap_engine.config import parse_retention_days

    assert parse_retention_days(None) == 90
    assert parse_retention_days("") == 90
    assert parse_retention_days("  30 ") == 30
    assert parse_retention_days("0") == 0  # keep forever


def test_parse_retention_days_rejects_garbage_with_named_var() -> None:
    """A typo'd value fails startup loudly with the env var named —
    consistent with DAP_LOG_LEVEL and DAP_CORS_ORIGINS policy."""
    from dap_engine.config import parse_retention_days

    with pytest.raises(ValueError, match="DAP_INTERACTION_LOG_RETENTION_DAYS"):
        parse_retention_days("ninety")
