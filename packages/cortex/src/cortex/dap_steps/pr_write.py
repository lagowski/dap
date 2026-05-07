"""DAP side-effect step for Phase 3 PR operations.

Handles create_issue_comment and log_git_op calls that were removed from
pr_creator.py and pr_merger.py as part of the DAP decoupling refactor (#224).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["run_side_effects"]


def run_side_effects(state: dict, audit: dict) -> dict:
    """Post the issue comment linking to the created PR.

    Args:
        state: Current CortexState dict.
        audit: The ``__audit`` dict from pr_creator's return value.
            Expected key: ``pr_url``.

    Returns:
        Empty dict (no state changes).
    """
    from cortex.config.settings import load_settings
    from cortex.tools.github import create_issue_comment

    pr_url = audit.get("pr_url", "")
    if not pr_url:
        return {}

    settings = load_settings()
    code_token = settings.get_github_token("code")

    create_issue_comment.invoke(
        {
            "repo": state["repo"],
            "issue_number": state["issue_number"],
            "body": f"PR created: {pr_url}",
            "token": code_token,
        }
    )

    return {}
