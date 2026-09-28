"""The dashboard pins its pnpm version so every tool regenerates the lockfile alike.

Without `packageManager`, Dependabot regenerates apps/dashboard/pnpm-lock.yaml with
its own default pnpm. pnpm 11 no longer reads the `"pnpm"` field of package.json, so
it drops the `overrides:` block and every `--frozen-lockfile` install then fails with
ERR_PNPM_LOCKFILE_CONFIG_MISMATCH (#884, #888, #895, #900, #903). Dependabot honours
`packageManager`, so the pin keeps it on the same pnpm major as CI and the Dockerfile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE_JSON = ROOT / "apps" / "dashboard" / "package.json"


def _pinned_version() -> str:
    manager = json.loads(PACKAGE_JSON.read_text())["packageManager"]
    match = re.fullmatch(r"pnpm@(\d+\.\d+\.\d+)", manager)
    assert match, f"packageManager must pin an exact pnpm version, got {manager!r}"
    return match.group(1)


def test_dashboard_pins_pnpm_9() -> None:
    # pnpm 9 and 10 both read package.json "pnpm".overrides; 11 does not.
    assert _pinned_version().split(".")[0] == "9"


def test_ci_workflows_use_the_pinned_major() -> None:
    major = _pinned_version().split(".")[0]
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for declared in re.findall(r'PNPM_VERSION:\s*"([^"]+)"', workflow.read_text()):
            assert declared.split(".")[0] == major, f"{workflow.name}: PNPM_VERSION {declared}"


def test_dockerfile_activates_the_pinned_version() -> None:
    activated = re.findall(r"corepack prepare pnpm@(\S+)", (ROOT / "Dockerfile").read_text())
    assert activated == [_pinned_version()]
