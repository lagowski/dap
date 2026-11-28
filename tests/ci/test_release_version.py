"""The workspace is prepared for the v0.4.1 release (#1045, #1070).

Runs ``scripts/check-release-metadata.py`` and pins the release family it
reports, so a partial bump (one package left behind, a stale ``>=0.3,<0.4``
first-party pin) or a forgotten ``__version__`` fails here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VERSION = "0.4.1"

INSTALL_DOCS = (
    Path("README.md"),
    Path("docs/quick-start.md"),
    Path("examples/standalone/README.md"),
    Path("examples/standalone/docker-compose.yml"),
    Path("examples/standalone/.env.example"),
)


def test_release_metadata_reports_expected_version() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check-release-metadata.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"Release metadata OK for {EXPECTED_VERSION}" in result.stdout
    assert "First-party compatibility spec: >=0.4,<0.5" in result.stdout


def test_runtime_version_strings_match_release() -> None:
    for path in (
        Path("apps/engine/src/dap_engine/version.py"),
        Path("apps/cli/src/dap_cli/__init__.py"),
    ):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert f'__version__ = "{EXPECTED_VERSION}"' in text, path


def test_database_package_matches_release() -> None:
    text = (REPO_ROOT / "packages/database/pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(EXPECTED_VERSION)}"$', text, re.M)
    engine = (REPO_ROOT / "apps/engine/pyproject.toml").read_text(encoding="utf-8")
    assert '"dap-database>=0.4,<0.5"' in engine


def test_install_docs_carry_no_stale_release_tag() -> None:
    for path in INSTALL_DOCS:
        full = REPO_ROOT / path
        if not full.exists():
            continue
        assert "0.3.0" not in full.read_text(encoding="utf-8"), path
