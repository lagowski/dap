"""Runtime execution policy helpers.

The adapter layer deliberately stays user-agnostic: it receives a
``RuntimeTask`` and executes it. Multi-user authorization belongs in the
engine before a task reaches an adapter.
"""

from __future__ import annotations

BASH_RUNTIME_ID = "bash"


class RuntimePolicyError(ValueError):
    """Raised when a runtime is unavailable to the current actor."""


def runtime_policy_error(
    runtime_id: str,
    *,
    is_admin: bool,
    allow_bash_runtime_for_non_admin: bool,
) -> str | None:
    """Return an operator-facing denial reason, or ``None`` when allowed."""
    if runtime_id != BASH_RUNTIME_ID:
        return None
    if is_admin or allow_bash_runtime_for_non_admin:
        return None
    return (
        "bash runtime is disabled for non-admin users. "
        "Set DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1 to opt in."
    )


def ensure_runtime_allowed(
    runtime_id: str,
    *,
    is_admin: bool,
    allow_bash_runtime_for_non_admin: bool,
) -> None:
    """Raise when ``runtime_id`` is not permitted for this actor."""
    reason = runtime_policy_error(
        runtime_id,
        is_admin=is_admin,
        allow_bash_runtime_for_non_admin=allow_bash_runtime_for_non_admin,
    )
    if reason is not None:
        raise RuntimePolicyError(reason)
