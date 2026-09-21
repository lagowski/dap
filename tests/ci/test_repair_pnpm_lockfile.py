from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_repair() -> ModuleType:
    path = Path(__file__).parents[2] / ".github" / "scripts" / "repair_pnpm_lockfile.py"
    spec = importlib.util.spec_from_file_location("repair_pnpm_lockfile", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair: Any = _load_repair()

MISMATCH = (
    ' ERR_PNPM_LOCKFILE_CONFIG_MISMATCH  Cannot proceed with the frozen installation. '
    'The current "overrides" configuration doesn\'t match the value found in the lockfile'
)
OUTDATED = " ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with \"frozen-lockfile\" because pnpm-lock.yaml is not up to date"


def test_clean_frozen_check_needs_no_repair() -> None:
    assert repair.classify_frozen_result(0, "Done in 2.8s using pnpm v9.15.9") == "ok"


def test_exit_code_wins_over_error_text_in_output() -> None:
    # A passing check is left alone even if the text happens to mention an error code.
    assert repair.classify_frozen_result(0, MISMATCH) == "ok"


def test_overrides_dropped_from_lockfile_is_repaired() -> None:
    # The failure Dependabot produced on #888 and #900.
    assert repair.classify_frozen_result(1, MISMATCH) == "repair"


def test_outdated_lockfile_is_repaired() -> None:
    assert repair.classify_frozen_result(1, OUTDATED) == "repair"


def test_unrelated_failure_is_not_repaired() -> None:
    # Network, registry or auth failures must surface, never trigger a push.
    assert repair.classify_frozen_result(1, " ERR_PNPM_FETCH_404  GET https://registry.npmjs.org/x: Not Found") == "error"


def test_failure_without_a_pnpm_error_code_is_not_repaired() -> None:
    assert repair.classify_frozen_result(1, "") == "error"
    assert repair.classify_frozen_result(137, "Killed") == "error"
