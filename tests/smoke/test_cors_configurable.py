"""Tests for the CORS allow-list configuration (#259).

Production deployments need to point the dashboard at custom origins
without editing the engine source. ``EngineConfig.cors_origins``
overrides the local-dev defaults; ``DAP_CORS_ORIGINS`` is the env-var
seam that ``__main__`` uses to populate that field.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import (
    DEFAULT_CORS_ORIGINS,
    EngineConfig,
    create_app,
    parse_cors_origins,
)
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_db() -> Iterator[str]:
    with tempfile.TemporaryDirectory(prefix="dap-cors-") as tmp:
        yield str(Path(tmp) / "state.db")


def _allowed_origin_for(client: TestClient, origin: str) -> str | None:
    """Send a GET with an ``Origin`` header and return what CORSMiddleware
    answered. ``None`` means the origin was rejected — the middleware
    omits the response header for disallowed origins."""
    response = client.get("/health", headers={"Origin": origin})
    value = response.headers.get("access-control-allow-origin")
    return value if value is None else str(value)


# ---------------------------------------------------------------------------
# parse_cors_origins (env-var parser)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),  # whitespace-only → empty after strip → None
        (",,", None),  # commas-only → all entries empty → None
        ("https://a.com", ["https://a.com"]),
        ("https://a.com,https://b.com", ["https://a.com", "https://b.com"]),
        (
            "  https://a.com , https://b.com ",
            ["https://a.com", "https://b.com"],
        ),
        ("https://a.com,,https://b.com", ["https://a.com", "https://b.com"]),
        ("https://a.com,", ["https://a.com"]),  # trailing comma tolerated
    ],
)
def test_parse_cors_origins(raw: str | None, expected: list[str] | None) -> None:
    assert parse_cors_origins(raw) == expected


# ---------------------------------------------------------------------------
# Default behaviour — no override means local-dev list applies
# ---------------------------------------------------------------------------


def test_default_origins_accept_dashboard_localhost(tmp_db: str) -> None:
    app = create_app(EngineConfig(db_path=tmp_db))
    with TestClient(app) as client:
        allowed = _allowed_origin_for(client, "http://localhost:3000")
    assert allowed == "http://localhost:3000"


def test_default_origins_reject_unknown_origin(tmp_db: str) -> None:
    app = create_app(EngineConfig(db_path=tmp_db))
    with TestClient(app) as client:
        allowed = _allowed_origin_for(client, "https://untrusted.example.com")
    assert allowed is None


# ---------------------------------------------------------------------------
# Override via EngineConfig
# ---------------------------------------------------------------------------


def test_explicit_cors_origins_replaces_defaults(tmp_db: str) -> None:
    """A custom ``cors_origins`` list replaces the local-dev defaults
    entirely — the previously-allowed dashboard origin no longer matches."""
    app = create_app(EngineConfig(db_path=tmp_db, cors_origins=["https://prod.example.com"]))
    with TestClient(app) as client:
        prod_allowed = _allowed_origin_for(client, "https://prod.example.com")
        dev_allowed = _allowed_origin_for(client, "http://localhost:3000")
    assert prod_allowed == "https://prod.example.com"
    assert dev_allowed is None


def test_default_cors_origins_constant_matches_legacy_list() -> None:
    """The exported default list must keep the documented local-dev origins
    so existing setups (dashboard on :3000, alt :7332) keep working without
    any operator action."""
    assert "http://localhost:3000" in DEFAULT_CORS_ORIGINS
    assert "http://127.0.0.1:3000" in DEFAULT_CORS_ORIGINS
    assert "http://localhost:7332" in DEFAULT_CORS_ORIGINS
    assert "http://127.0.0.1:7332" in DEFAULT_CORS_ORIGINS
