"""Retry-prepare node — bridge from a failed tester run back to coder.

When the post-tester router decides to retry (tests failed, retry budget
not exhausted), this node increments the retry counter so the next loop
iteration knows it's a retry, and surfaces the failed test names so
``run_execution_node`` can prepend them to coder's user_prompt (#89).
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["run"]


async def run(state: dict, config: dict) -> dict:
    """Increment retry counter and record what's being retried."""
    retry_count = state.get("pr_merger_retry_count", 0) + 1
    tester_result = state.get("tester_result", {}) or {}
    failed = tester_result.get("failed", 0)
    errors = tester_result.get("errors", 0)
    failed_tests = tester_result.get("failed_tests", [])

    return {
        "pr_merger_retry_count": retry_count,
        "current_phase": "retry_prepare",
        "files_changed": None,  # reset accumulator; next coder round starts clean
        "decisions": [
            *state.get("decisions", []),
            {
                "node": "retry_prepare",
                "action": "retry",
                "reasoning": f"attempt {retry_count}: looping back to coder with "
                f"{failed} failed + {errors} errors "
                f"({len(failed_tests)} named test failures)",
                "backend": "",
                "model": "",
                "tokens": "",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ],
    }
