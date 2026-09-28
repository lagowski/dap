"""The dashboard pins its pnpm version so every tool regenerates the lockfile alike.

Without `packageManager`, Dependabot regenerates apps/dashboard/pnpm-lock.yaml with
its own default pnpm. pnpm 11 no longer reads the `"pnpm"` field of package.json, so
it drops the `overrides:` block and every `--frozen-lockfile` install then fails with
ERR_PNPM_LOCKFILE_CONFIG_MISMATCH (#884, #888, #895, #900, #903). Dependabot honours
`packageManager`, so the pin keeps it on the same pnpm major as CI and the Dockerfile.

Upgrading off pnpm 9: the pin is a ceiling, not a preference. pnpm 10+ reads overrides
from pnpm-workspace.yaml (`overrides:`) and pnpm 11 ignores package.json "pnpm".overrides
entirely. Before raising the pin, move the overrides into apps/dashboard/pnpm-workspace.yaml
and confirm the regenerated lockfile still has its `overrides:` block, or all the next and
postcss security pins are silently lost again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE_JSON = ROOT / "apps" / "dashboard" / "package.json"
WORKFLOWS_DECLARING_PNPM = {
    "ci.yml",
    "dependabot-lockfile-repair.yml",
    "docker-build.yml",
    "e2e.yml",
    "release.yml",
}


def _pinned_version() -> str:
    manager = json.loads(PACKAGE_JSON.read_text())["packageManager"]
    match = re.fullmatch(r"pnpm@(\d+\.\d+\.\d+)", manager)
    assert match, f"packageManager must pin an exact pnpm version, got {manager!r}"
    return match.group(1)


def test_dashboard_pins_pnpm_9() -> None:
    # pnpm 9 and 10 both read package.json "pnpm".overrides; 11 does not. See the module
    # docstring before changing this: the overrides must move to pnpm-workspace.yaml first.
    assert _pinned_version().split(".")[0] == "9"


def test_ci_workflows_use_the_pinned_major() -> None:
    major = _pinned_version().split(".")[0]
    declared = {
        workflow.name: re.findall(r'PNPM_VERSION:\s*"([^"]+)"', workflow.read_text())
        for workflow in (ROOT / ".github" / "workflows").glob("*.yml")
    }
    declared = {name: versions for name, versions in declared.items() if versions}
    # An exact set, so a renamed, unquoted or moved PNPM_VERSION fails here instead of
    # leaving nothing to check.
    assert set(declared) == WORKFLOWS_DECLARING_PNPM
    for name, versions in declared.items():
        for version in versions:
            assert version.split(".")[0] == major, f"{name}: PNPM_VERSION {version}"


def test_dockerfile_activates_the_pinned_version() -> None:
    activated = re.findall(r"corepack prepare pnpm@(\S+)", (ROOT / "Dockerfile").read_text())
    assert activated == [_pinned_version()]


def test_install_hints_activate_the_pinned_version() -> None:
    # `pnpm@latest` is pnpm 11, which warns that it ignores "pnpm".overrides and invites
    # the wrong fix. Every hint telling a developer how to install pnpm names the pin.
    hints = [
        ROOT / "README.md",
        ROOT / "scripts" / "setup",
        ROOT / "scripts" / "build-dashboard-bundle.sh",
    ]
    for hint in hints:
        activated = re.findall(r"corepack prepare pnpm@(\S+?)\s", hint.read_text())
        assert activated == [_pinned_version()], f"{hint.relative_to(ROOT)}: {activated}"
