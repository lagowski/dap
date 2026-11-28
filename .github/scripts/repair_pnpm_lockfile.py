"""Repair a pnpm lockfile that Dependabot regenerated inconsistently.

Dependabot drops the `overrides:` block from apps/dashboard/pnpm-lock.yaml when
it regenerates it, while package.json still declares pnpm.overrides. Every job
that runs `pnpm install --frozen-lockfile` then aborts with
ERR_PNPM_LOCKFILE_CONFIG_MISMATCH before doing any work (#888, #900).

This helper runs the same frozen check CI runs. It regenerates the lockfile only
when that check fails with a lockfile-consistency error, and confirms the check
passes afterwards. Any other failure is surfaced, never repaired. No package
scripts run: every pnpm call passes --ignore-scripts and --lockfile-only.

Usage: repair_pnpm_lockfile.py <directory containing package.json>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

REPAIRABLE_ERRORS = (
    "ERR_PNPM_LOCKFILE_CONFIG_MISMATCH",
    "ERR_PNPM_OUTDATED_LOCKFILE",
)
FROZEN_CHECK = ["pnpm", "install", "--frozen-lockfile", "--lockfile-only", "--ignore-scripts"]
REGENERATE = ["pnpm", "install", "--lockfile-only", "--ignore-scripts"]

Verdict = Literal["ok", "repair", "error"]
EXPECTED_ARGC = 2  # program name + directory


def classify_frozen_result(returncode: int, output: str) -> Verdict:
    """Decide what to do with the result of the frozen-lockfile check."""
    if returncode == 0:
        return "ok"
    if any(code in output for code in REPAIRABLE_ERRORS):
        return "repair"
    return "error"


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def main(argv: list[str]) -> int:
    if len(argv) != EXPECTED_ARGC:
        print(__doc__, file=sys.stderr)
        return 2
    directory = Path(argv[1])

    returncode, output = _run(FROZEN_CHECK, directory)
    verdict = classify_frozen_result(returncode, output)
    if verdict == "ok":
        print(f"{directory}: lockfile is consistent, nothing to repair")
        return 0
    if verdict == "error":
        print(output, file=sys.stderr)
        print(
            f"::error::{directory}: frozen-lockfile check failed for a reason this helper "
            "does not repair; not touching the lockfile",
            file=sys.stderr,
        )
        return 1

    returncode, output = _run(REGENERATE, directory)
    if returncode != 0:
        print(output, file=sys.stderr)
        print(f"::error::{directory}: lockfile regeneration failed", file=sys.stderr)
        return 1

    returncode, output = _run(FROZEN_CHECK, directory)
    if returncode != 0:
        print(output, file=sys.stderr)
        print(
            f"::error::{directory}: lockfile still fails the frozen check after regeneration",
            file=sys.stderr,
        )
        return 1

    print(f"{directory}: lockfile repaired, frozen-lockfile check now passes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
