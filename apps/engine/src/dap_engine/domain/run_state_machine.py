"""Explicit run state machine — one source of truth for ``final_status`` transitions.

Before #778 Phase 2 the allowed-status sets lived as string literals
scattered across ``api/run_lifecycle.py`` (abort/pause/resume),
``api/run_interventions.py`` (approve/retry/skip) and
``persistence/runs.py`` (terminal short-circuits, atomic claims).
A drift between any two of those sites would produce 409s that
disagree with what the DB-level claim actually allows.

The graph (statuses are ``dap_types.FinalStatus`` values)::

    running ──pause──────────▶ paused
    running ──finalize───────▶ success | failed | aborted
    paused  ──resume/approve─▶ running
    paused  ──gate timeout───▶ failed
    paused  ──abort──────────▶ aborted
    failed  ──retry/skip─────▶ running     (node-level revive, #260)
    success / aborted        ▶ (terminal, no outgoing transitions)

The HTTP endpoints fast-fail with 409 using the named sets below; the
actual transition stays an atomic UPDATE in ``persistence/runs.py``
whose WHERE clause uses the same sets (TOCTOU safety, #185).
"""

from __future__ import annotations

from typing import Final, get_args

from dap_types import FinalStatus

__all__ = [
    "ABORTABLE_STATUSES",
    "PAUSABLE_STATUSES",
    "RESUMABLE_STATUSES",
    "REVIVABLE_STATUSES",
    "RUN_STATUSES",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "can_transition",
    "is_terminal",
]

RUN_STATUSES: Final[frozenset[FinalStatus]] = frozenset(get_args(FinalStatus))

# Once a run lands in any of these, ``finalize_run`` / ``pause_run``
# short-circuit so a stray late cancel can't overwrite the recorded
# outcome (#257). ``failed`` is terminal for the *lifecycle* endpoints
# but revivable via node-level retry/skip — see REVIVABLE_STATUSES.
TERMINAL_STATUSES: Final[frozenset[FinalStatus]] = frozenset({"success", "failed", "aborted"})

# Full transition graph. Endpoint sets below are views over this graph
# (asserted consistent in tests); keep both in sync when adding states.
TRANSITIONS: Final[dict[FinalStatus, frozenset[FinalStatus]]] = {
    "running": frozenset({"paused", "success", "failed", "aborted"}),
    "paused": frozenset({"running", "failed", "aborted"}),
    "failed": frozenset({"running"}),
    "success": frozenset(),
    "aborted": frozenset(),
}

# POST /runs/{id}/abort — paused runs can also be aborted.
ABORTABLE_STATUSES: Final[frozenset[FinalStatus]] = frozenset({"running", "paused"})

# POST /runs/{id}/pause
PAUSABLE_STATUSES: Final[frozenset[FinalStatus]] = frozenset({"running"})

# POST /runs/{id}/resume and /nodes/{n}/approve — atomic claim in
# ``try_claim_resume`` uses the same set.
RESUMABLE_STATUSES: Final[frozenset[FinalStatus]] = frozenset({"paused"})

# POST /runs/{id}/nodes/{n}/retry|skip — wider than resume so a
# node-level intervention can put a terminated run back in motion;
# atomic claim in ``try_claim_revive`` uses the same set.
REVIVABLE_STATUSES: Final[frozenset[FinalStatus]] = frozenset({"paused", "failed"})


def is_terminal(status: str) -> bool:
    """True when ``status`` is a recorded outcome that must not be overwritten."""
    return status in TERMINAL_STATUSES


def can_transition(from_status: str, to_status: str) -> bool:
    """True when the state graph allows ``from_status`` → ``to_status``.

    Unknown statuses yield ``False`` rather than raising — callers treat
    a malformed status the same as an illegal transition (409).
    """
    allowed = TRANSITIONS.get(from_status)  # type: ignore[call-overload]
    return allowed is not None and to_status in allowed
