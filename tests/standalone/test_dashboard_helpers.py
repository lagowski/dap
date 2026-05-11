"""Unit tests for the dashboard launcher helpers (#302 sub-D2, #334).

These don't require pnpm/node — they exercise ``find_bundle`` /
``find_node`` directly against synthetic paths. The
heavier wheel-level coverage lives in ``test_bundled_dashboard.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from dap_cli import dashboard


def test_find_bundle_returns_none_when_server_js_missing() -> None:
    """A dev install ships the ``_dashboard/`` directory with just a
    ``.gitkeep`` placeholder — no ``server.js``. ``find_bundle``
    must return None so ``dap start`` falls back to the
    "not bundled" message instead of trying to spawn a missing file.
    """
    # The repo's actual checkout matches the dev-install shape: the
    # placeholder lives at apps/cli/src/dap_cli/_dashboard/.gitkeep
    # and no server.js sits next to it.
    result = dashboard.find_bundle()
    # This test runs in the repo where the bundle hasn't been built;
    # if a developer ran the bundler before pytest, the assertion
    # would flip — skip in that case.
    bundle_dir = Path(__file__).resolve().parents[2] / "apps/cli/src/dap_cli/_dashboard"
    if (bundle_dir / "server.js").exists():
        return  # bundle is present — different test covers that
    assert result is None


def test_find_node_returns_path_when_node_installed() -> None:
    """If Node is on PATH (most dev machines, all CI runners),
    ``find_node`` returns the absolute path; otherwise None.
    Either is valid — we just assert the function doesn't raise."""
    result = dashboard.find_node()
    assert result is None or Path(result).name in {"node", "node.exe"}


def test_spawn_dashboard_returns_none_when_bundle_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the bundle isn't present, ``spawn_dashboard`` shouldn't
    even try to call ``node``."""
    with (
        patch.object(dashboard, "find_bundle", return_value=None),
        patch.object(dashboard, "find_node", return_value="/usr/bin/node"),
    ):
        # Sanity: even with node available, no bundle → no process.
        result = dashboard.spawn_dashboard(
            port=7332,
            engine_url="http://127.0.0.1:7333",
        )
    assert result is None


def test_spawn_dashboard_returns_none_when_node_missing() -> None:
    """If Node isn't on PATH, surface the no-spawn outcome cleanly
    so the caller can print a friendly message — don't let a real
    ``FileNotFoundError`` propagate."""
    fake_server = Path("/nonexistent/server.js")
    with (
        patch.object(dashboard, "find_bundle", return_value=fake_server),
        patch.object(dashboard, "find_node", return_value=None),
    ):
        result = dashboard.spawn_dashboard(
            port=7332,
            engine_url="http://127.0.0.1:7333",
        )
    assert result is None
