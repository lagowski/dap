"""PR Merger node — merge a PR after tests pass and reviewer approves.

Phase 3 (merge). Uses GH_TOKEN_MERGE (rlagowski — DIFFERENT USER from pr_creator).
Backend: ollama/gemma4. Refuses to merge unless both gates pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.config.settings import load_settings
from cortex.tools.github import merge_pull_request

__all__ = ["run"]


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

    # Gate: refuse to merge unless both conditions are met
    if not tests_passed:
        return preserve_extensions(cortex_to_dap({
            "merged": False,
            "merge_sha": "",
            "current_phase": "pr_merger_refused",
            "error": "Cannot merge: tests did not pass",
            "__audit": _audit_base,
            "decisions": state.get("decisions", []) + [{
                "node": "pr_merger",
                "action": "refused",
                "reasoning": "Tests did not pass — merge blocked",
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": now,
            }],
        }), original_extensions)

    if not review_approved:
        return preserve_extensions(cortex_to_dap({
            "merged": False,
            "merge_sha": "",
            "current_phase": "pr_merger_refused",
            "error": "Cannot merge: reviewer did not approve",
            "__audit": _audit_base,
            "decisions": state.get("decisions", []) + [{
                "node": "pr_merger",
                "action": "refused",
                "reasoning": "Reviewer did not approve — merge blocked",
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": now,
            }],
        }), original_extensions)

    if not pr_number:
        return preserve_extensions(cortex_to_dap({
            "merged": False,
            "merge_sha": "",
            "current_phase": "pr_merger_failed",
            "error": "No PR number in state",
            "__audit": _audit_base,
            "decisions": state.get("decisions", []) + [{
                "node": "pr_merger",
                "action": "failed",
                "reasoning": "No PR number available in state",
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": now,
            }],
        }), original_extensions)

    # Use MERGE token (rlagowski — different user from code token)
    settings = load_settings()
    merge_token = settings.get_github_token("merge")

    merge_result = merge_pull_request.invoke({
        "repo": repo,
        "pr_number": pr_number,
        "token": merge_token,
    })

    if "error" in merge_result:
        return preserve_extensions(cortex_to_dap({
            "merged": False,
            "merge_sha": "",
            "current_phase": "pr_merger_failed",
            "error": merge_result["error"],
            "__audit": _audit_base,
            "decisions": state.get("decisions", []) + [{
                "node": "pr_merger",
                "action": "failed",
                "reasoning": merge_result["error"],
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": now,
            }],
        }), original_extensions)

    merge_sha = merge_result.get("sha", "")

    return preserve_extensions(cortex_to_dap({
        "merged": True,
        "merge_sha": merge_sha,
        "current_phase": "pr_merger_complete",
        "__audit": _audit_base,
        "decisions": state.get("decisions", []) + [{
            "node": "pr_merger",
            "action": "merged",
            "reasoning": f"PR #{pr_number} merged with sha {merge_sha}",
            "backend": "",
            "model": "",
            "tokens": "",
            "timestamp": now,
        }],
    }), original_extensions)
