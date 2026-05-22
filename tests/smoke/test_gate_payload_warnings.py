"""Unit tests for _gate_payload_from_interrupt finalize_warnings inclusion.

Verifies that ``_gate_payload_from_interrupt`` surfaces
``finalize_warnings`` from the gate state's extensions in the payload
stored on the Run row (#583).
"""

from __future__ import annotations

from typing import Any

from dap_engine.execution.run_orchestrator import _gate_payload_from_interrupt
from dap_engine.execution.runner import RunnerInterrupt


def _make_interrupt(gate_state: dict[str, Any] | None) -> RunnerInterrupt:
    """Build a RunnerInterrupt with a given gate_state dict."""
    exc = RunnerInterrupt(next_nodes=["phase1_gate"])
    exc.gate_state = gate_state
    return exc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_payload_includes_finalize_warnings_when_present() -> None:
    """finalize_warnings in extensions must appear in the gate payload."""
    warnings = [
        "stale open question already answered by mockup",
        "cicd references non-existent tests/standalone/",
    ]
    interrupt = _make_interrupt(
        {
            "extensions": {
                "task_assignments": [{"agent": "coder", "task": "implement feature"}],
                "finalize_warnings": warnings,
            },
        }
    )
    payload = _gate_payload_from_interrupt(interrupt)
    assert payload is not None
    assert payload["finalize_warnings"] == warnings


def test_payload_omits_finalize_warnings_when_absent() -> None:
    """When no finalize_warnings exist, the key must not appear in the payload."""
    interrupt = _make_interrupt(
        {
            "extensions": {
                "task_assignments": [{"agent": "coder", "task": "implement feature"}],
            },
        }
    )
    payload = _gate_payload_from_interrupt(interrupt)
    assert payload is not None
    assert "finalize_warnings" not in payload


def test_payload_omits_finalize_warnings_when_empty_list() -> None:
    """An empty warnings list is falsy — should not appear in payload."""
    interrupt = _make_interrupt(
        {
            "extensions": {
                "task_assignments": [{"agent": "coder", "task": "implement feature"}],
                "finalize_warnings": [],
            },
        }
    )
    payload = _gate_payload_from_interrupt(interrupt)
    assert payload is not None
    assert "finalize_warnings" not in payload


def test_payload_none_when_no_gate_state() -> None:
    """No gate_state → payload is None (existing behavior preserved)."""
    interrupt = _make_interrupt(None)
    assert _gate_payload_from_interrupt(interrupt) is None


def test_payload_none_when_no_task_assignments() -> None:
    """No task_assignments → payload is None even if warnings exist."""
    interrupt = _make_interrupt(
        {
            "extensions": {
                "finalize_warnings": ["some warning"],
            },
        }
    )
    assert _gate_payload_from_interrupt(interrupt) is None


def test_payload_includes_spec_and_warnings_together() -> None:
    """Both spec and finalize_warnings can coexist in the payload."""
    warnings = ["minor AC wording issue"]
    interrupt = _make_interrupt(
        {
            "extensions": {
                "task_assignments": [{"agent": "coder", "task": "fix bug"}],
                "spec": "## Specification\nFix the authentication bug.",
                "finalize_warnings": warnings,
            },
        }
    )
    payload = _gate_payload_from_interrupt(interrupt)
    assert payload is not None
    assert payload["task_assignments"] == [{"agent": "coder", "task": "fix bug"}]
    assert payload["spec"] == "## Specification\nFix the authentication bug."
    assert payload["finalize_warnings"] == warnings
