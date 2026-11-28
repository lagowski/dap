"""Gate node — a literal no-op.

A human-approval gate is just a target node the engine pauses *before* (via the
bundle's ``approval_required_nodes``). The pause is configured at the graph
level, never by calling pause from inside the node — doing so loops forever.
So the gate body does nothing.
"""

from __future__ import annotations

from typing import Any


def noop(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Do nothing — the pause happens at the graph level, before this runs."""
    return {}
