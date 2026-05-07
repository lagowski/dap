"""Reviewer node — review a PR and approve or reject.

Phase 3 (merge). Uses GH_TOKEN_READ. Backend: claude_cli.
Reads the issue and PR context, outputs approve/reject with comments.

Cross-checks the actual git diff against the Technical Specification's
Target Files and flags contradictions (files listed but not touched,
criteria claimed but not addressed). Issue #96.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import UTC, datetime

import time

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.backends.base import LLMRequest
from cortex.backends.registry import create_backend
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.init.profile import repo_clone_path
from cortex.templates import get_issue_section, parse_target_files
from cortex.tools.github import read_issue

logger = logging.getLogger(__name__)

__all__ = ["run"]

SYSTEM_PROMPT = """\
You are the Reviewer agent. Review the pull request for:
- Code quality and correctness
- Security issues
- Test coverage
- Architecture alignment

**Operator gate approvals override issue-body blockers.** If the operator has
approved the Phase 1 and Phase 2 human gates for this run, any BLOCKER comments
in the issue body have been explicitly acknowledged and accepted. Do NOT block
the PR on acknowledged blockers — the operator's gate approval is the override
signal. Your role is to assess code quality and correctness, not to re-litigate
business decisions the operator has already made.

Write your full review first. Your response MUST end with exactly one of
these two lines, on its own line, with no markdown decoration:

    DECISION: APPROVE
    DECISION: REJECT

IMPORTANT: These are the ONLY valid verdicts. If the code needs work before
approval, output DECISION: REJECT with specific feedback in your review body.
NEVER use NEEDS_WORK, REQUEST CHANGES, or any other format. NEVER omit the
DECISION line. Trust only the provided diff for file-existence claims — do
NOT assert that files are missing if they appear in the diff.

Heading-style verdicts (e.g. `### Verdict: REQUEST CHANGES`,
`## Reviewer Agent: APPROVE`, `**REJECT**`) are parsed but trigger a warning
that hides your reasoning from the operator. Do NOT use them. Do NOT wrap
the DECISION line in bold/italic markdown. Do NOT write DECISION at the
start of your response — conclude with it.
"""

# Primary: matches "DECISION: APPROVE/REJECT" and "VERDICT: APPROVE/REJECT"
# (with optional bold markdown). Case-insensitive; "REJECTED" → REJECT.
_DECISION_PATTERN = re.compile(
    r"\b(?:DECISION|VERDICT):\s*\*{0,2}(APPROVE|REJECT)(?:ED)?\*{0,2}",
    re.IGNORECASE,
)
# Fallback: matches heading-style verdicts like "## Reviewer Agent: APPROVE"
# that the LLM sometimes writes instead of the canonical DECISION: form.
_HEADING_VERDICT_PATTERN = re.compile(
    r"^#+\s+[^:\n]+:\s*\*{0,2}(APPROVE|REJECT)(?:ED)?\*{0,2}",
    re.IGNORECASE | re.MULTILINE,
)
# When neither pattern fires, surface the operator's likely intent by
# logging the first sentence containing one of these keywords (#184).
_VERDICT_KEYWORD_PATTERN = re.compile(
    r"[^.!?\n]*\b(?:verdict|decision|approve|reject|request\s+changes)\b[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)


def _parse_review(text: str) -> tuple[bool | None, str]:
    """Parse reviewer LLM response into (approved, comments).

    Returns ``None`` for ``approved`` when no DECISION:/VERDICT: line is found,
    so callers can distinguish "no verdict" from an explicit reject. Uses the
    LAST matching line so that a draft "REJECTED: let me look…" at the start
    does not override the final conclusion written later.
    """
    approved: bool | None = None
    for m in _DECISION_PATTERN.finditer(text):
        approved = m.group(1).upper().startswith("APPROVE")
    # If the canonical pattern didn't fire, try the heading-style fallback
    # (e.g. "## Reviewer Agent: APPROVE"). Last match still wins.
    if approved is None:
        for m in _HEADING_VERDICT_PATTERN.finditer(text):
            approved = m.group(1).upper().startswith("APPROVE")
    # No verdict found — return None so the caller can trigger a re-prompt
    # rather than silently defaulting to False.
    if approved is None:
        logger.warning(
            "reviewer: no DECISION:/VERDICT: line found in response "
            "(%d chars)", len(text)
        )
        # Surface the likely intent so operators see the reviewer's verdict
        # at the gate without DB queries (#184).
        hint_match = _VERDICT_KEYWORD_PATTERN.search(text)
        if hint_match:
            hint = hint_match.group(0).strip()
            if hint:
                logger.warning(
                    "reviewer: likely intent (first verdict-keyword sentence): %s",
                    hint[:300],
                )

    comments_match = re.search(r"COMMENTS:\s*(.+)", text, re.DOTALL)
    comments = comments_match.group(1).strip() if comments_match else text[:500]

    return approved, comments


def _get_diff(workspace: str, base: str, branch: str) -> str:
    """Run git diff and return the output."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{base}...{branch}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("git diff failed: %s", exc)
        return ""


def _diff_files(diff_output: str) -> set[str]:
    """Extract the set of file paths from a unified diff."""
    files: set[str] = set()
    for line in diff_output.split("\n"):
        if line.startswith("+++ b/"):
            files.add(line[6:])
        elif line.startswith("--- a/"):
            files.add(line[6:])
    # Remove /dev/null (new/deleted files show this)
    files.discard("/dev/null")
    return files


def _build_contradictions(
    target_files: list[tuple[str, int | None]],
    diff_files_set: set[str],
) -> list[dict]:
    """Find Target Files not touched by the diff."""
    contradictions: list[dict] = []
    for path, line_num in target_files:
        if path not in diff_files_set:
            contradictions.append({
                "path": path,
                "line": line_num,
                "reason": "listed in Target Files but not touched in diff",
            })
    return contradictions


def _format_contradictions(contradictions: list[dict]) -> str:
    """Build a markdown ### Contradictions block."""
    lines = ["### Contradictions"]
    for c in contradictions:
        ref = f"`{c['path']}:{c['line']}`" if c["line"] else f"`{c['path']}`"
        lines.append(f"- {ref} — {c['reason']}")
    return "\n".join(lines)


async def run(state: dict, config: dict) -> dict:
    """Review the PR and decide approve/reject."""
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    settings = load_settings()
    token = settings.get_github_token("read")

    # Read issue for context
    issue_data = read_issue.invoke(
        {"repo": state["repo"], "issue_number": state["issue_number"], "token": token}
    )

    pr_number = state.get("pr_number", 0)
    pr_url = state.get("pr_url", "")
    branch_name = state.get("branch_name", "unknown")
    issue_body = issue_data.get("body", "")

    # Get actual diff from workspace (issue #96)
    diff_content = ""
    workspace = repo_clone_path(state["repo"])
    if workspace.exists() and branch_name != "unknown":
        diff_content = _get_diff(str(workspace), "main", branch_name)

    # Cross-check Target Files against diff
    spec_section = get_issue_section(issue_body, "Technical Specification")
    target_files = parse_target_files(spec_section)
    changed_files = _diff_files(diff_content) if diff_content else set()
    contradictions = (
        _build_contradictions(target_files, changed_files)
        if target_files and changed_files
        else []
    )

    # Build prompt with diff content included
    diff_block = f"\n\nActual diff:\n```\n{diff_content[:8000]}\n```" if diff_content else ""

    # Surface acknowledged findings so the LLM doesn't re-block on them (#244).
    acknowledged_findings = state.get("acknowledged_findings") or []
    acknowledged_block = ""
    if acknowledged_findings:
        items = "\n".join(f"- {f}" for f in acknowledged_findings)
        acknowledged_block = (
            f"\n\nAcknowledged findings (operator already approved these — "
            f"do NOT re-block on them):\n{items}"
        )

    user_prompt = f"""Review PR #{pr_number}: {pr_url}

Original issue #{state['issue_number']}: {issue_data.get('title', '')}

Issue body:
{issue_body}

Branch: {branch_name}
Files changed: {', '.join(state.get('files_changed') or [])}
Tests passed: {state.get('tests_passed', False)}
Test output: {state.get('test_output', 'N/A')}{diff_block}{acknowledged_block}

Review this PR and decide whether to approve or reject."""

    agent_config = get_agent_backend_config("reviewer")
    backend = create_backend(agent_config)
    system_prompt = agent_config.get("system_prompt", SYSTEM_PROMPT)

    start_ms = int(time.monotonic() * 1000)
    response = backend.invoke(LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=agent_config.get("temperature", 0.1),
        max_tokens=agent_config.get("max_tokens", 4000),
    ))
    duration_ms = int(time.monotonic() * 1000) - start_ms

    approved, comments = _parse_review(response.content)

    # Second-chance re-prompt when no DECISION line was found (#202).
    if approved is None:
        logger.warning("reviewer: no DECISION line — triggering second-chance re-prompt")
        reprompt_text = (
            "Your previous response had no DECISION line. "
            "Respond with only: DECISION: APPROVE or DECISION: REJECT"
        )
        start_ms2 = int(time.monotonic() * 1000)
        response2 = backend.invoke(LLMRequest(
            system_prompt=system_prompt,
            user_prompt=reprompt_text,
            temperature=0.0,
            max_tokens=50,
        ))
        duration_ms2 = int(time.monotonic() * 1000) - start_ms2

        approved2, comments2 = _parse_review(response2.content)

        if approved2 is not None:
            approved = approved2
            comments = comments2
        else:
            approved = False
            logger.warning(
                "reviewer: second-chance re-prompt also returned no DECISION "
                "— defaulting to REJECT"
            )

    # Append contradictions subsection if any
    review_output = response.content
    if contradictions:
        review_output += "\n\n" + _format_contradictions(contradictions)

    return preserve_extensions(cortex_to_dap({
        "review_approved": approved,
        "review_output": review_output,
        "current_phase": "reviewer_complete",
        "__audit": {
            "tokens_used": (
                (response.input_tokens or 0) + (response.output_tokens or 0)
            ),
            "cost_usd": response.cost_usd,
            "section": "reviewer",
            "backend": response.backend,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "duration_ms": duration_ms,
        },
        "decisions": state.get("decisions", []) + [{
            "node": "reviewer",
            "action": "reviewed",
            "reasoning": f"{'APPROVED' if approved else 'REJECTED'}: {comments}",
            "backend": response.backend,
            "model": response.model,
            "tokens": f"{response.input_tokens}in/{response.output_tokens}out",
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }), original_extensions)
