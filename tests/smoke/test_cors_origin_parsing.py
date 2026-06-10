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
