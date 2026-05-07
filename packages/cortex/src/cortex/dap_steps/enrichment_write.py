"""DAP side-effect step for Phase 1 enrichment nodes.

Separates GitHub issue writes and audit logging from the pure run() functions
so DAP can retry the LLM call without duplicating external writes.

Usage (in DAP pipeline config):
    Add as a python-func node after each Phase 1 LLM node. The runtime_config
    must include ``agent_name`` and ``section_name``; both are passed via the
    ``config`` dict by the DAP python-func runtime (func(state, config)).
"""

from __future__ import annotations

import logging

from cortex.adapters.pipeline_state import dap_to_cortex
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.persistence.audit import log_decision, log_issue_update
from cortex.templates import get_issue_section, update_issue_section
from cortex.tools.github import create_issue_comment, read_issue, update_issue_body

logger = logging.getLogger(__name__)

__all__ = ["run_side_effects"]


async def run_side_effects(
    state: dict,
    config: dict,
    _agent_name: str = "",
    _section_name: str = "",
) -> dict:
    """Execute the GitHub write and audit side effects for a Phase 1 node.

    Supports two calling conventions:

    DAP (python-func runtime)::

        run_side_effects(state, config)
        # config = {"agent_name": "mockup", "section_name": "Description", ...}

    Internal (Phase 1 nodes in native Cortex run) — backwards-compatible::

        run_side_effects(state, audit, "mockup", "Description")
        # audit dict passed as config; agent_name/section_name as positional args

    Returns:
        Dict with ``issue_comments`` from the re-read issue, and ``error`` if
        the GitHub write failed.
    """
    if _agent_name:
        # Old internal calling convention: (state, audit, agent_name, section_name)
        state = {**state, "__audit": config}
        config = {"agent_name": _agent_name, "section_name": _section_name}
    state = dap_to_cortex(state)
    agent_name: str = config.get("agent_name", "")
    section_name: str = config.get("section_name", "")
    audit: dict = state.get("__audit") or {}
    settings = load_settings()
    agent_config = get_agent_backend_config(agent_name)
    token = settings.get_github_token(agent_config["github_token_role"])

    section_content = audit.get("content_after", "")

    # Re-read current issue body for update
    issue_data = read_issue.invoke(
        {
            "repo": state["repo"],
            "issue_number": state["issue_number"],
            "token": token,
        }
    )
    current_body = issue_data.get("body", "")
    issue_comments = issue_data.get("comments", [])
    content_before = get_issue_section(current_body, section_name)

    # Update the section in the issue body
    new_body = update_issue_section(current_body, section_name, section_content)
    update_result = update_issue_body.invoke(
        {
            "repo": state["repo"],
            "issue_number": state["issue_number"],
            "body": new_body,
            "token": token,
        }
    )
    update_failed = isinstance(update_result, dict) and "error" in update_result
    if update_failed:
        logger.error(
            "dap_steps/enrichment_write: %s failed to update_issue_body for "
            "section %r (token role=%s): %s",
            agent_name,
            section_name,
            agent_config.get("github_token_role", "?"),
            update_result.get("error", "unknown"),
        )

    # Post completion comment
    words_before = len(content_before.split()) if content_before else 0
    words_after = len(section_content.split()) if section_content else 0
    enrichment_comment = (
        f"**{agent_name}** completed {section_name} ({words_before} → {words_after} words)"
    )
    comment_url = create_issue_comment.invoke(
        {
            "repo": state["repo"],
            "issue_number": state["issue_number"],
            "body": enrichment_comment,
            "token": token,
        }
    )
    comment_failed = isinstance(comment_url, str) and comment_url.startswith("error:")
    if comment_failed:
        logger.warning(
            "dap_steps/enrichment_write: %s failed to post completion comment (token role=%s): %s",
            agent_name,
            agent_config.get("github_token_role", "?"),
            comment_url,
        )

    # Audit logging
    run_id = state.get("run_id", "")
    if run_id:
        log_decision(
            run_id=run_id,
            agent=agent_name,
            backend=audit.get("backend", ""),
            model=audit.get("model", ""),
            phase="enrichment",
            system_prompt="",
            user_prompt="",
            response=section_content,
            input_tokens=audit.get("input_tokens", 0),
            output_tokens=audit.get("output_tokens", 0),
            project_id=state.get("project_id"),
            cost_usd=audit.get("cost_usd", 0.0),
            duration_ms=audit.get("duration_ms", 0),
        )
        if not update_failed:
            log_issue_update(
                run_id=run_id,
                agent=agent_name,
                issue_number=state["issue_number"],
                section=section_name,
                content_before=content_before,
                content_after=section_content,
                github_comment_url=comment_url if not comment_failed else None,
            )

    result: dict = {"issue_comments": issue_comments}
    if update_failed:
        result["error"] = (
            f"{agent_name} could not update_issue_body for "
            f"{section_name!r}: {update_result.get('error', '?')}"
        )
    return result
