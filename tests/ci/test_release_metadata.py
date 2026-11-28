"""Every first-party dependency of a published package is itself published (#1070).

dap-engine 0.4.0 required ``dap-database``, a workspace member that
release.yml never built or uploaded, so ``pipx install dap-cli==0.4.0``
could not resolve. ``scripts/check-release-metadata.py`` must refuse that.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/check-release-metadata.py"
WORKFLOW = REPO_ROOT / ".github/workflows/release.yml"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_metadata", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_check_lists_dap_database_as_published() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "  - dap-database" in result.stdout


def test_check_rejects_unpublished_first_party_dependency() -> None:
    module = _load_script()
    without_database = tuple(
        package for package in module.RELEASE_PACKAGES if package.distribution != "dap-database"
    )
    errors = module.check(without_database)
    assert any("dap-database" in error and "not published" in error for error in errors), errors


def test_check_passes_for_the_real_published_set() -> None:
    module = _load_script()
    assert module.check(module.RELEASE_PACKAGES) == []


def test_release_workflow_builds_every_published_package() -> None:
    module = _load_script()
    text = WORKFLOW.read_text(encoding="utf-8")
    loops = [line for line in text.splitlines() if line.strip().startswith("for pkg in ")]
    assert len(loops) >= 2, loops
    for package in module.RELEASE_PACKAGES:
        package_dir = str(package.path.parent)
        for loop in loops:
            assert f" {package_dir}" in loop, (package_dir, loop)
    assert "skip-existing: true" in text
