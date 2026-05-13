"""Copilot Reviewer node — wait for Copilot PR review, fix comments, verify.

Phase 3 (merge). Uses GH_TOKEN_READ to poll, GH_TOKEN_CODE to push fixes.
Runs between pr-creator and reviewer:

    pr-creator → copilot-reviewer → reviewer → gate-phase3 → pr-merger

Behaviour:
1. Poll GitHub until Copilot (copilot-pull-request-reviewer[bot]) posts a
   review on the PR (timeout: POLL_TIMEOUT_SEC).
2. If Copilot left inline comments, call the coder backend to fix them and
   push the fixes to the coder branch.
3. Re-poll up to MAX_FIX_ROUNDS times until Copilot is satisfied or
   max rounds exhausted.
4. Write copilot_approved / copilot_comments to state so the human gate
   (gate-phase3) and reviewer can surface Copilot's verdict.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from github import Auth, Github

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.backends.base import BackendError, LLMRequest
from cortex.backends.registry import create_backend_with_fallback as create_backend
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.init.profile import repo_clone_path
from cortex.nodes.execution import (
    _clean_workspace_for_agent,
    _push_branch,
    checkout_agent_branch,
)

logger = logging.getLogger(__name__)

__all__ = ["run"]

# How long to wait for Copilot to post its first review (seconds).
POLL_TIMEOUT_SEC = 600  # 10 minutes
POLL_INTERVAL_SEC = 15

# Max rounds of fix → re-review.
MAX_FIX_ROUNDS = 2

COPILOT_BOT = "copilot-pull-request-reviewer[bot]"

FIX_SYSTEM_PROMPT = """\
You are the Copilot Fix agent. Address inline code review comments left by
GitHub Copilot on a pull request.

RULES:
- Fix ONLY the specific issues Copilot flagged — do NOT refactor unrelated code
- Use the Edit tool for existing files, Write for new ones
- After edits, stage + commit:
  `git add <files> && git commit -m "fix: address Copilot review comments"`
- Do NOT push — the pipeline handles that
- Output one line per comment addressed: "fixed <file>:<line> — <brief>"
"""


def _get_copilot_comments(repo_full: str, pr_number: int, token: str) -> tuple[list[dict], bool]:
    """Return (inline_comments, copilot_approved).

    inline_comments: list of {path, line, body} dicts from Copilot's review.
    copilot_approved: True if Copilot's latest review state is APPROVED.
    """
    gh = Github(auth=Auth.Token(token))
    repo = gh.get_repo(repo_full)
    pr = repo.get_pull(pr_number)

    copilot_approved = False
    inline_comments: list[dict] = []

    for review in pr.get_reviews():
        if review.user.login != COPILOT_BOT:
            continue
        # Track latest state
        if review.state == "APPROVED":
            copilot_approved = True
            inline_comments = []  # approved — no more action needed
        else:
            copilot_approved = False

    if copilot_approved:
        return [], True

    # Pull inline comments
    for comment in pr.get_review_comments():
        if comment.user.login != COPILOT_BOT:
            continue
        inline_comments.append(
            {
                "path": comment.path,
                "line": comment.position or comment.original_position,
                "body": comment.body,
            }
        )

    return inline_comments, False


def _poll_for_copilot(repo_full: str, pr_number: int, token: str) -> tuple[list[dict], bool]:
    """Poll until Copilot posts a review or POLL_TIMEOUT_SEC elapses.

    Returns (inline_comments, copilot_approved).
    """
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        comments, approved = _get_copilot_comments(repo_full, pr_number, token)
        if approved:
            logger.info("copilot_reviewer: Copilot approved the PR")
            return [], True
        if comments:
            logger.info("copilot_reviewer: Copilot left %d comment(s)", len(comments))
            return comments, False
        logger.debug(
            "copilot_reviewer: no Copilot review yet, retrying in %ds",
            POLL_INTERVAL_SEC,
        )
        time.sleep(POLL_INTERVAL_SEC)

    logger.warning(
        "copilot_reviewer: timed out after %ds waiting for Copilot review — "
        "proceeding without Copilot approval",
        POLL_TIMEOUT_SEC,
    )
    return [], False


def _build_fix_prompt(comments: list[dict], repo: str, branch: str) -> str:
    lines = [
        f"Fix the following Copilot review comments on branch `{branch}` in repo `{repo}`:",
        "",
    ]
    for i, c in enumerate(comments, 1):
        lines.append(f"**Comment {i}** — `{c['path']}` line {c['line']}:")
        lines.append(c["body"].strip())
        lines.append("")
    lines.append("Address every comment. Commit the fixes when done.")
    return "\n".join(lines)


def _run_fix(
    comments: list[dict],
    repo: str,
    branch_name: str,
    token: str,
    workspace: Path,
) -> bool:
    """Call the coder backend to fix Copilot's comments and push.

    Returns True if fixes were committed + pushed successfully.
    """
    agent_config = get_agent_backend_config("coder")
    # Override cwd to workspace so claude_cli edits the right files
    agent_config = dict(agent_config)
    agent_config["cwd"] = str(workspace)
    backend = create_backend(agent_config)

    checkout_agent_branch(workspace, branch_name, branch_name)
    _clean_workspace_for_agent(workspace)

    try:
        response = backend.invoke(
            LLMRequest(
                system_prompt=FIX_SYSTEM_PROMPT,
                user_prompt=_build_fix_prompt(comments, repo, branch_name),
                temperature=0.1,
                max_tokens=4000,
            )
        )
    except BackendError as e:
        logger.error("copilot_reviewer: fix backend failed: %s", e)
        return False

    logger.info(
        "copilot_reviewer: fix backend finished (%s/%s, %d+%d tokens)",
        response.backend,
        response.model,
        response.input_tokens or 0,
        response.output_tokens or 0,
    )

    push_err = _push_branch(workspace, repo, branch_name, token)
    if push_err:
        logger.error("copilot_reviewer: push after fix failed: %s", push_err)
        return False
    return True


async def run(state: dict, config: dict) -> dict:
    """Wait for Copilot review, fix comments, verify — up to MAX_FIX_ROUNDS."""
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)

    settings = load_settings()
    read_token = settings.get_github_token("read")
    code_token = settings.get_github_token("code")

    repo = state["repo"]
    pr_number = int(state.get("pr_number") or 0)
    branch_name = str(state.get("coder_branch_name") or state.get("branch_name") or "")

    def _decision(action: str, reasoning: str) -> dict:
        return {
            "node": "copilot_reviewer",
            "action": action,
            "reasoning": reasoning,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    if not pr_number:
        logger.warning("copilot_reviewer: no pr_number in state — skipping")
        return preserve_extensions(
            cortex_to_dap(
                {
                    "copilot_approved": False,
                    "copilot_comments": [],
                    "decisions": [
                        *state.get("decisions", []),
                        _decision("skipped", "no pr_number in state"),
                    ],
                }
            ),
            original_extensions,
        )

    workspace = repo_clone_path(repo)

    comments, approved = _poll_for_copilot(repo, pr_number, read_token)

    if approved:
        return preserve_extensions(
            cortex_to_dap(
                {
                    "copilot_approved": True,
                    "copilot_comments": [],
                    "decisions": [
                        *state.get("decisions", []),
                        _decision("approved", "Copilot approved the PR"),
                    ],
                }
            ),
            original_extensions,
        )

    # Fix loop
    fix_round = 0
    addressed_comments: list[str] = []
    timed_out = not comments  # timed out with no review at all

    while comments and fix_round < MAX_FIX_ROUNDS:
        fix_round += 1
        logger.info(
            "copilot_reviewer: fix round %d/%d — %d comment(s)",
            fix_round,
            MAX_FIX_ROUNDS,
            len(comments),
        )
        addressed_comments.extend(f"{c['path']}:{c['line']} — {c['body'][:80]}" for c in comments)
        if workspace.exists():
            _run_fix(comments, repo, branch_name, code_token, workspace)

        # Re-poll for updated review
        comments, approved = _poll_for_copilot(repo, pr_number, read_token)
        if approved:
            break

    action = (
        "approved_after_fix"
        if approved
        else "timed_out"
        if timed_out
        else f"max_rounds_reached ({MAX_FIX_ROUNDS})"
    )

    verdict = (
        f"Copilot approved after {fix_round} fix round(s)"
        if approved
        else (
            f"Addressed {len(addressed_comments)} comment(s), "
            f"Copilot verdict: "
            f"{'pending/no review' if timed_out else 'still requesting changes'}"
        )
    )
    return preserve_extensions(
        cortex_to_dap(
            {
                "copilot_approved": approved,
                "copilot_comments": addressed_comments,
                "decisions": [
                    *state.get("decisions", []),
                    _decision(action, verdict),
                ],
            }
        ),
        original_extensions,
    )
