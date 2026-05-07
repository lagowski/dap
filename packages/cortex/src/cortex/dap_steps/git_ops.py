"""DAP side-effect step for Phase 2 git operations.

Handles log_git_op calls that were removed from run_execution_node() in
cortex/nodes/execution.py as part of the DAP decoupling refactor (#224).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["run_side_effects"]


def run_side_effects(state: dict, audit: dict, agent_name: str) -> dict:
    """Log git operations from a Phase 2 execution node run.

    Args:
        state: Current CortexState dict.
        audit: The ``__audit`` dict from the execution node's return value.
            Expected keys: ``branch_name``, ``branch_details``,
            ``commits_pushed`` (optional).
        agent_name: Agent key (e.g., "coder", "designer", "documenter").

    Returns:
        Empty dict (no state changes).
    """
    from cortex.persistence.audit import log_git_op

    run_id = state.get("run_id", "")
    repo = state.get("repo", "")
    branch_name = audit.get("branch_name", "")

    if not (run_id and branch_name):
        return {}

    log_git_op(
        run_id=run_id or None,
        agent=agent_name,
        operation="create_branch",
        repo=repo,
        branch=branch_name,
        github_user="Dixter999",
        details=audit.get("branch_details", {}),
    )

    commits_pushed = audit.get("commits_pushed", 0)
    if commits_pushed:
        log_git_op(
            run_id=run_id or None,
            agent=agent_name,
            operation="push_branch",
            repo=repo,
            branch=branch_name,
            github_user="Dixter999",
            details={"commits": commits_pushed},
        )

    return {}
