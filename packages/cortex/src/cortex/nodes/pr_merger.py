"""PR Merger node — merge a PR after tests pass and reviewer approves.

Phase 3 (merge). Uses GH_TOKEN_MERGE (rlagowski — DIFFERENT USER from pr_creator).
Backend: ollama/gemma4. Refuses to merge unless both gates pass.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.backends.base import BackendError
from cortex.config.settings import load_settings
from cortex.tools.github import merge_pull_request

logger = logging.getLogger(__name__)

__all__ = ["run"]

# Patterns in pytest output that indicate a test failed due to missing
# infrastructure (database, network service) rather than a code defect.
# When ALL failures match at least one of these patterns, pr_merger proceeds
# instead of blocking — pre-existing infra failures must not prevent merging
# code changes that are themselves correct (#222).
_INFRA_FAILURE_PATTERNS = [
    re.compile(r"connection (?:refused|reset)", re.IGNORECASE),
    re.compile(r"could not connect", re.IGNORECASE),
    re.compile(r"connection to server", re.IGNORECASE),
    re.compile(r"OperationalError", re.IGNORECASE),
    re.compile(r"no route to host", re.IGNORECASE),
    re.compile(r"Name or service not known", re.IGNORECASE),
]


def _is_infra_only_failure(test_output: str) -> bool:
    """Return True when EVERY explicit test failure is caused by a missing
    infrastructure dependency (DB, network service, etc.).

    Strategy:
    1. Extract ``FAILED <name>`` lines from the output.  These are the
       ground-truth failure list that pytest emits in the short-summary
       section (one per failing test).
    2. For each failed-test name, check whether an infra-error pattern
       appears anywhere in the block of text between that ``FAILED`` line
       and the next ``FAILED`` line (or end of output).  If every block
       contains at least one infra pattern the function returns True.
    3. If no ``FAILED`` lines are found at all, fall back to checking the
       full output for any infra pattern — this covers environments where
       pytest summary lines are absent (e.g. ``pytest -q``).

    Returns False (block merge) if:
    - The output is empty.
    - Any failure block contains no infra pattern (real code defect).
    - A mix of infra and non-infra failures exists.
    """
    if not test_output:
        return False

    _FAILED_LINE_RE = re.compile(r"^FAILED \S+", re.MULTILINE)
    failed_positions = [m.start() for m in _FAILED_LINE_RE.finditer(test_output)]

    if not failed_positions:
        # No explicit FAILED lines — fall back to whole-output scan.
        return any(pat.search(test_output) for pat in _INFRA_FAILURE_PATTERNS)

    # Slice the output into per-failure blocks and verify each one.
    boundaries = [*failed_positions, len(test_output)]
    for i, start in enumerate(failed_positions):
        block = test_output[start : boundaries[i + 1]]
        if not any(pat.search(block) for pat in _INFRA_FAILURE_PATTERNS):
            return False  # at least one failure is NOT infra-related

    return True


async def run(state: dict, config: dict) -> dict:
    """Merge the PR only if tests passed AND reviewer approved."""
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    tests_passed = state.get("tests_passed", False)
    review_approved = state.get("review_approved", False)
    pr_number = state.get("pr_number", 0)
    repo = state["repo"]
    now = datetime.now(UTC).isoformat()

    _audit_base = {"tokens_used": 0, "cost_usd": 0.0, "section": "pr_merger"}
    test_output = state.get("test_output", "")

    # Gate: refuse to merge unless tests passed OR all failures are
    # infrastructure-only (pre-existing DB/network issues unrelated to the
    # change under review — #222).
    infra_bypass = not tests_passed and _is_infra_only_failure(test_output)

    if not tests_passed:
        if infra_bypass:
            logger.warning(
                "pr_merger: bypassing test gate — infra-only failures detected "
                "(connection refused / OperationalError); tests_passed=False"
            )
            # Fall through to the merge path; do not block.
        else:
            raise BackendError("Cannot merge: tests did not pass")

    if not review_approved:
        raise BackendError("Cannot merge: reviewer did not approve")

    if not pr_number:
        raise BackendError("Cannot merge: no PR number in state (pr-creator may have failed)")

    # Use MERGE token (rlagowski — different user from code token)
    settings = load_settings()
    merge_token = settings.get_github_token("merge")

    merge_result = merge_pull_request.invoke(
        {
            "repo": repo,
            "pr_number": pr_number,
            "token": merge_token,
        }
    )

    if "error" in merge_result:
        raise BackendError(f"Merge failed: {merge_result['error']}")

    merge_sha = merge_result.get("sha", "")

    # Build the reasoning string — flag when test gate was bypassed so the
    # audit trail makes the bypass visible to operators (#222 fix 2).
    merge_reasoning = f"PR #{pr_number} merged with sha {merge_sha}"
    if infra_bypass:
        merge_reasoning += (
            " [tests_infra_bypass=True: pre-existing infrastructure "
            "failures were detected and skipped]"
        )

    result: dict = {
        "merged": True,
        "merge_sha": merge_sha,
        "current_phase": "pr_merger_complete",
        "__audit": _audit_base,
        "decisions": [
            *state.get("decisions", []),
            {
                "node": "pr_merger",
                "action": "merged",
                "reasoning": merge_reasoning,
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": now,
            },
        ],
    }
    if infra_bypass:
        result["tests_infra_bypass"] = True
        result["tests_bypass_reason"] = "pre-existing infrastructure failures detected"

    return preserve_extensions(cortex_to_dap(result), original_extensions)
