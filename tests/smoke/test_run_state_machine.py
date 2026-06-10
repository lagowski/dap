"""Unit tests for the explicit run state machine (#778 Phase 2).

One source of truth for which ``final_status`` transitions are legal —
previously the allowed-status sets were string literals scattered across
``api/run_lifecycle.py``, ``api/run_interventions.py`` and
``persistence/runs.py``.
"""

from __future__ import annotations

from typing import get_args

from dap_engine.domain.run_state_machine import (
    ABORTABLE_STATUSES,
    PAUSABLE_STATUSES,
    RESUMABLE_STATUSES,
    REVIVABLE_STATUSES,
    RUN_STATUSES,
    TERMINAL_STATUSES,
    TRANSITIONS,
    can_transition,
    is_terminal,
)
from dap_types import FinalStatus


def test_run_statuses_match_canonical_literal() -> None:
    """The machine must cover exactly the ``FinalStatus`` literal values."""
    assert RUN_STATUSES == frozenset(get_args(FinalStatus))


def test_terminal_statuses() -> None:
    assert TERMINAL_STATUSES == frozenset({"success", "failed", "aborted"})
    for status in TERMINAL_STATUSES:
        assert is_terminal(status)
    for status in RUN_STATUSES - TERMINAL_STATUSES:
        assert not is_terminal(status)


def test_terminal_statuses_have_no_outgoing_transitions_except_failed_revive() -> None:
    """``success``/``aborted`` are dead ends; ``failed`` can only be revived."""
    assert TRANSITIONS["success"] == frozenset()
    assert TRANSITIONS["aborted"] == frozenset()
    assert TRANSITIONS["failed"] == frozenset({"running"})


def test_running_transitions() -> None:
    """A running run can pause or land on any terminal status."""
    assert TRANSITIONS["running"] == frozenset({"paused", "success", "failed", "aborted"})


def test_paused_transitions() -> None:
    """Paused resumes to running, fails on gate expiry, or aborts."""
    assert TRANSITIONS["paused"] == frozenset({"running", "failed", "aborted"})


def test_can_transition_matrix() -> None:
    assert can_transition("running", "paused")
    assert can_transition("running", "success")
    assert can_transition("paused", "running")
    assert can_transition("paused", "failed")  # gate timeout
    assert can_transition("failed", "running")  # node-level revive
    assert not can_transition("success", "running")
    assert not can_transition("aborted", "running")
    assert not can_transition("running", "running")
    assert not can_transition("failed", "success")


def test_named_endpoint_sets_are_consistent_with_the_graph() -> None:
    """Each endpoint's allowed-status set must agree with TRANSITIONS."""
    for status in ABORTABLE_STATUSES:
        assert can_transition(status, "aborted")
    for status in PAUSABLE_STATUSES:
        assert can_transition(status, "paused")
    for status in RESUMABLE_STATUSES:
        assert can_transition(status, "running")
    for status in REVIVABLE_STATUSES:
        assert can_transition(status, "running")


def test_named_endpoint_sets_match_documented_behaviour() -> None:
    """Pin the exact sets the HTTP endpoints enforce (409 otherwise)."""
    assert ABORTABLE_STATUSES == frozenset({"running", "paused"})
    assert PAUSABLE_STATUSES == frozenset({"running"})
    assert RESUMABLE_STATUSES == frozenset({"paused"})
    assert REVIVABLE_STATUSES == frozenset({"paused", "failed"})
