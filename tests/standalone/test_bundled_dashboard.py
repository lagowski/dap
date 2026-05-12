"""Wheel-level test for the bundled dashboard (#302 sub-D2, #334).

Runs ``scripts/build-dashboard-bundle.sh`` then ``uv build`` against
``apps/cli``, inspects the produced wheel without installing it, and
asserts the dashboard bundle landed in the right place.

Marked ``slow`` because it shells out to ``pnpm install`` + ``pnpm
build`` (each ~30s on a warm cache, longer cold). CI runs it on
docker-build's same trigger so packaging regressions land alongside
the Dockerfile that depends on the same artefacts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "build-dashboard-bundle.sh"
CLI_DIR = REPO_ROOT / "apps" / "cli"


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


pytestmark = pytest.mark.skipif(
    not (_have("pnpm") and _have("node")),
    reason="dashboard bundle test requires pnpm + node on PATH",
)


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the dashboard bundle + the dap-cli wheel once per session.

    The fixture is module-scoped because the build is expensive and
    every assertion here looks at the same artefact. Using
    ``tmp_path_factory`` keeps the wheel out of the repo so
    re-running the test suite doesn't leave staged files behind.
    """
    # Stage the bundle into apps/cli/src/dap_cli/_dashboard/.
    subprocess.run([str(SCRIPT)], check=True, cwd=REPO_ROOT)

    # Build the dap-cli wheel into a temp dir.
    out_dir = tmp_path_factory.mktemp("dap-cli-wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        check=True,
        cwd=CLI_DIR,
    )
    wheels = list(out_dir.glob("dap_cli-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_wheel_ships_dashboard_bundle(built_wheel: Path) -> None:
    """The wheel must carry ``_dashboard/server.js`` so ``dap start``
    can spawn the bundled dashboard."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    # The exact path matches hatchling's ``force-include`` mapping.
    assert "dap_cli/_dashboard/server.js" in names, sorted(n for n in names if "_dashboard" in n)


def test_wheel_ships_dashboard_static_assets(built_wheel: Path) -> None:
    """Next standalone needs ``.next/static`` next to ``server.js`` —
    without it the dashboard renders blank pages."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    static_files = [n for n in names if n.startswith("dap_cli/_dashboard/.next/static/")]
    assert len(static_files) > 0, (
        "no .next/static files in the wheel — build-dashboard-bundle.sh didn't copy them correctly"
    )


def test_wheel_ships_bundle_info(built_wheel: Path) -> None:
    """The build script writes ``BUNDLE_INFO.txt`` with the build
    timestamp + git SHA — useful for tracing which commit a wheel
    came from. Confirm it lands in the artefact."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
        assert "dap_cli/_dashboard/BUNDLE_INFO.txt" in names
        info = zf.read("dap_cli/_dashboard/BUNDLE_INFO.txt").decode()
    assert "DAP dashboard bundle" in info
    assert "Git SHA:" in info


def test_find_bundle_after_install(built_wheel: Path, tmp_path: Path) -> None:
    """Install the wheel into a fresh venv and exercise the runtime
    ``find_bundle()`` helper. Catches the kind of packaging bug
    where the wheel has the file but ``importlib.resources`` can't
    see it (wrong package data declaration, missing __init__.py
    etc.)."""
    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / ("Scripts" if os.name == "nt" else "bin") / "pip"
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    # ``--no-deps`` because dap-cli's runtime deps (dap-engine,
    # uvicorn, …) aren't published yet — this test only cares
    # whether the wheel exposes the bundle, not whether a full
    # install works.
    subprocess.run(
        [str(pip), "install", "--quiet", "--no-deps", str(built_wheel)],
        check=True,
    )
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from dap_cli.dashboard import find_bundle; "
            "p = find_bundle(); "
            "print('FOUND' if p else 'MISSING'); "
            "print(p)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.startswith("FOUND"), probe.stdout
    assert probe.stdout.strip().endswith("server.js"), probe.stdout
