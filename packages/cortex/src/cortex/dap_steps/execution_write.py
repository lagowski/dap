"""DAP side-effect step for Phase 2 execution issue writes.

Handles update_issue_body, create_issue_comment, and log_issue_update calls
that were removed from run_execution_node() in cortex/nodes/execution.py as
part of the DAP decoupling refactor (#224).
"""

from __future__ import annotations

import logging

from cortex.persistence.audit import log_issue_update
from cortex.tools.github import create_issue_comment, update_issue_body

logger = logging.getLogger(__name__)

__all__ = ["run_side_effects"]


def run_side_effects(
    state: dict,
    audit: dict,
    agent_name: str,
    *,
    token: str,
    status_line: str,
    existing_section: str,
    updated_body: str,
    comment_body: str,
) -> dict:
    """Write execution status to GitHub and log the update.

    Args:
        state: Current CortexState dict.
        audit: The ``__audit`` dict from run_execution_node's return value.
        agent_name: Agent key (e.g., "coder", "designer", "documenter").
        token: GitHub token for code writes.
        status_line: The new status line added for this agent.
        existing_section: Previous Execution Status section content.
        updated_body: The full issue body with updated Execution Status section.
        comment_body: Completion comment body to post.

    Returns:
        Dict with optional ``error`` key if the GitHub write failed.
    """
    repo = state["repo"]
    issue_number = state["issue_number"]
    run_id = state.get("run_id", "")

    result: dict = {}

    # 1. Update Execution Status section in the issue body
    update_result = update_issue_body.invoke(
        {
            "repo": repo,
            "issue_number": issue_number,
            "body": updated_body,
            "token": token,
        }
    )
    update_failed = isinstance(update_result, dict) and "error" in update_result
    if update_failed:
        logger.error(
            "Agent %s failed to update Execution Status (token role=code): %s",
            agent_name,
            update_result.get("error"),
        )
        result["error"] = update_result.get("error", "update failed")

    # 2. Audit the section update (issue #53)
    if not update_failed and run_id:
        log_issue_update(
            run_id=run_id,
            agent=agent_name,
            issue_number=issue_number,
            section="Execution Status",
            content_before=existing_section,
            content_after=status_line,
            github_comment_url=None,
        )

    # 3. Post completion comment
    comment_result = create_issue_comment.invoke(
        {"repo": repo, "issue_number": issue_number, "body": comment_body, "token": token}
    )
    if isinstance(comment_result, str) and comment_result.startswith("error:"):
        logger.warning(
            "Agent %s failed to post execution comment (token role=code): %s",
            agent_name,
            comment_result,
        )

    return result
