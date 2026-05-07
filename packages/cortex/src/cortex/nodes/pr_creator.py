"""PR Creator node — create a pull request linking to the issue.

Phase 3 (merge). Uses GH_TOKEN_CODE (Dixter999). Backend: ollama/gemma4.
Creates a PR from the working branch, posts a comment on the issue.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.backends.base import LLMRequest
from cortex.backends.registry import create_backend
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.tools.github import (
    create_pull_request,
    find_open_pr_for_branch,
    read_issue,
)

__all__ = ["run"]

SYSTEM_PROMPT = """\
You are the PR Creator agent. Write a pull request description.

Given the issue details and test results, produce:
TITLE: <short PR title referencing the issue>
BODY: <markdown PR body with summary of changes, test results, and Closes #N>

Keep it concise. Link to the original issue.
"""


async def run(state: dict, config: dict) -> dict:
    """Create a pull request and link it to the issue."""
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    settings = load_settings()
    code_token = settings.get_github_token("code")
    read_token = settings.get_github_token("read")

    repo = state["repo"]
    issue_number = state["issue_number"]
    branch_name = state.get("branch_name", "")

    # Read issue for context
    issue_data = read_issue.invoke(
        {"repo": repo, "issue_number": issue_number, "token": read_token}
    )

    user_prompt = f"""Issue #{issue_number}: {issue_data.get('title', '')}

{issue_data.get('body', '')}

Branch: {branch_name}
Files changed: {', '.join(state.get('files_changed') or [])}
Tests passed: {state.get('tests_passed', False)}
Test output: {state.get('test_output', 'N/A')}

Write a PR title and body."""

    agent_config = get_agent_backend_config("pr_creator")
    backend = create_backend(agent_config)
    system_prompt = agent_config.get("system_prompt", SYSTEM_PROMPT)

    response = backend.invoke(LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=agent_config.get("temperature", 0.1),
        max_tokens=agent_config.get("max_tokens", 2000),
    ))

    # Parse title/body from LLM response
    content = response.content
    title = f"Issue #{issue_number}: {issue_data.get('title', 'changes')}"
    body = f"Closes #{issue_number}\n\n{content}"

    if "TITLE:" in content:
        parts = content.split("BODY:", 1)
        title = parts[0].replace("TITLE:", "").strip()
        if len(parts) > 1:
            body = parts[1].strip()

    # Create the PR with CODE token (Dixter999)
    pr_result = create_pull_request.invoke({
        "repo": repo,
        "title": title,
        "body": body,
        "head": branch_name,
        "base": "main",
        "token": code_token,
    })

    audit = {
        "tokens_used": (response.input_tokens or 0) + (response.output_tokens or 0),
        "cost_usd": response.cost_usd,
        "section": "pr_creator",
        "backend": response.backend,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }

    if "error" in pr_result:
        # Recovery path (#139): the claude_cli backend sometimes creates
        # the PR itself via Bash/`gh pr create` before this Python code
        # gets to call create_pull_request. GitHub then returns
        # "Validation Failed: A pull request already exists" on our
        # second attempt. Look for an existing open PR matching the
        # branch and use it if found, instead of dropping the real PR
        # into state.error.
        existing = find_open_pr_for_branch(repo, branch_name, code_token)
        if existing is None:
            return preserve_extensions(cortex_to_dap({
                "error": pr_result["error"],
                "current_phase": "pr_creator_failed",
                "__audit": audit,
                "decisions": state.get("decisions", []) + [{
                    "node": "pr_creator",
                    "action": "failed",
                    "reasoning": pr_result["error"],
                    "backend": response.backend,
                    "model": response.model,
                    "tokens": f"{response.input_tokens}in/{response.output_tokens}out",
                    "timestamp": datetime.now(UTC).isoformat(),
                }],
            }), original_extensions)
        # Recovery succeeded — fall through to the success path with the
        # existing PR's number/url.
        pr_number = int(existing["number"])
        pr_url = str(existing["url"])
    else:
        pr_number = int(pr_result["number"])
        pr_url = pr_result["url"]

    # Note: issue comment ("PR created: <url>") is posted by
    # cortex/dap_steps/pr_write.py so this node stays side-effect-free.
    audit["pr_url"] = pr_url

    return preserve_extensions(cortex_to_dap({
        "pr_number": pr_number,
        "pr_url": pr_url,
        "current_phase": "pr_creator_complete",
        "__audit": audit,
        "decisions": state.get("decisions", []) + [{
            "node": "pr_creator",
            "action": "pr_created",
            "reasoning": f"PR #{pr_number} created: {pr_url}",
            "backend": response.backend,
            "model": response.model,
            "tokens": f"{response.input_tokens}in/{response.output_tokens}out",
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }), original_extensions)
