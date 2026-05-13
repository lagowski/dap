"""Tester node — verify tests pass before PR creation.

Phase 3 (merge). Runs the project's configured test command in the agent's
workspace branch and uses the exit code as ground truth. Replaces the
LLM-vibe-check that read empty ``state.commits`` and hallucinated about
whether work landed (issue #111).

Output (issue #89): both the legacy binary ``tests_passed`` and a richer
``tester_result`` dict with structured counts + failed test names. The
graph's post-tester router uses ``tester_result`` to decide whether to
loop back to coder for a bounded retry or proceed to PR creation.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import UTC, datetime

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.backends.base import BackendError
from cortex.init.profile import load_profile, profile_exists, repo_clone_path
from cortex.nodes.execution import checkout_agent_branch

logger = logging.getLogger(__name__)

__all__ = ["run"]


# Pytest summary line: "===== N failed, N passed, N error in N.NNs ====="
# We match the WHOLE summary line first so counts are extracted only from
# that line — not from error messages that happen to contain patterns like
# "port 30432 failed: Connection refused" (psycopg3 connection errors).
_PYTEST_SUMMARY_LINE_RE = re.compile(r"={2,}[^=\n]+ in \d+\.?\d*s[^=\n]*={2,}")
_PYTEST_PASSED_RE = re.compile(r"(\d+) passed")
_PYTEST_FAILED_RE = re.compile(r"(\d+) failed")
_PYTEST_ERROR_RE = re.compile(r"(\d+) error(?:s)?")
# Lines like "FAILED tests/test_widget.py::test_renders - assert ..." — pytest
# emits one per failure in the short summary above the totals line.
_PYTEST_FAILED_NAME_RE = re.compile(r"^FAILED (\S+)", re.MULTILINE)


def _parse_pytest_output(combined_output: str, returncode: int) -> dict:
    """Extract structured counts + failed test names from pytest text output.

    Returns a dict with keys ``passed``, ``failed``, ``errors``,
    ``failed_tests`` (list[str]). Used by ``pr_merger`` / the post-tester
    router for the deterministic gate (#89).

    The parser is pytest-format-specific. If the test command is something
    other than pytest (e.g. ``npm test``), counts default to 0/0/0 and
    ``failed_tests`` is empty — but ``returncode != 0`` still drives
    ``tests_passed=False``, so the binary gate keeps working.

    Counts are extracted only from the pytest summary line
    (``"===== N failed … in N.NNs ===="``) to avoid matching port numbers
    inside psycopg3 connection-error messages such as
    ``"connection to server … port 30432 failed: Connection refused"``.
    """
    # Find the LAST pytest summary line using finditer so that runs with
    # multiple pytest invocations (or aggregated project output) always parse
    # the final summary, not an earlier intermediate one.
    # Fall back to full output so the error-count heuristic still fires for
    # non-pytest runners that produce no summary line at all.
    summary_matches = list(_PYTEST_SUMMARY_LINE_RE.finditer(combined_output))
    summary_text = summary_matches[-1].group(0) if summary_matches else combined_output

    passed_match = _PYTEST_PASSED_RE.search(summary_text)
    failed_match = _PYTEST_FAILED_RE.search(summary_text)
    errors_match = _PYTEST_ERROR_RE.search(summary_text)
    failed_tests = _PYTEST_FAILED_NAME_RE.findall(combined_output)

    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    errors = int(errors_match.group(1)) if errors_match else 0

    # If the parser saw nothing but the run exited non-zero, that's a fatal
    # error before pytest could report — record it as an error so the gate
    # still treats it as failure rather than as a passing run with no tests.
    if returncode != 0 and passed == 0 and failed == 0 and errors == 0:
        errors = 1

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "failed_tests": failed_tests,
    }


def _collect_branch_diagnostics(workspace, base_branch: str) -> dict:
    """Capture HEAD SHA and commits-ahead-of-base for the workspace branch.

    Fetches ``origin/<base_branch>`` first so the rev-list comparison is
    against the current upstream, not whatever was last fetched. Returns
    a dict with:

    - ``sha`` — current HEAD of the workspace clone (the code that pytest
      will see). Empty string on git failure.
    - ``ahead`` — how many commits HEAD is ahead of ``origin/<base_branch>``.
      ``0`` means the branch matches base verbatim (e.g. coder created the
      branch but timed out before committing — the empirical #117 dogfood
      shape, #128). ``-1`` is the sentinel for "git failed; we don't know"
      so the caller can choose to proceed cautiously rather than mistreat
      a failed lookup as "no commits to test" (#129).
    """
    sha = ""
    ahead = -1
    try:
        # Pull a fresh view of base so the rev-list comparison is against
        # current origin, not stale local refs.
        subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            timeout=60,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        sha = (head.stdout or "").strip()
        count = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{base_branch}..HEAD"],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        count_str = (count.stdout or "").strip()
        # Empty stdout is treated as failure rather than zero — git either
        # printed nothing or the mock didn't supply a response. Falling
        # through to ahead=-1 lets the caller proceed cautiously instead
        # of mistreating "missing data" as "no commits, short-circuit".
        if not count_str or not sha:
            raise ValueError(f"incomplete diagnostics: sha={sha!r} count={count_str!r}")
        ahead = int(count_str)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        logger.warning(
            "branch diagnostics failed in %s vs origin/%s: %s",
            workspace,
            base_branch,
            stderr[:200] or e,
        )
    return {"sha": sha, "ahead": ahead}


def _run_tests(
    workspace,
    test_command: str,
    timeout_sec: int = 600,
    extra_env: dict[str, str] | None = None,
) -> tuple[bool, str, dict]:
    """Run ``test_command`` in ``workspace``. Return ``(passed, tail, result)``.

    ``passed`` reflects the subprocess exit code (0 = passed). ``tail`` is
    the last ~50 lines of combined stdout/stderr — enough to surface what
    failed without dumping a 10k-line pytest log into state. ``result`` is
    the structured pytest parse used by the deterministic gate (#89).

    ``extra_env`` is merged into the subprocess environment. Used to inject
    ``CORTEX_DATABASE_URL`` so DB-gated tests in workspace clones (which have
    no .env) can reach PostgreSQL and skip the @requires_db guards.
    """
    env = {**os.environ, **(extra_env or {})}
    try:
        result = subprocess.run(
            test_command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or "") + (e.stderr or "")
        tail = "\n".join(partial.splitlines()[-50:])
        # Timeout is unambiguously a failure — surface as an error in the
        # structured result so the retry router treats it like any other fail.
        return (
            False,
            f"TIMEOUT after {timeout_sec}s\n{tail}",
            {
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "failed_tests": [],
            },
        )

    combined = (result.stdout or "") + (result.stderr or "")
    tail = "\n".join(combined.splitlines()[-50:])
    parsed = _parse_pytest_output(combined, result.returncode)
    return result.returncode == 0, tail, parsed


async def run(state: dict, config: dict) -> dict:
    """Run the project's test command on the agent's branch in the workspace."""
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    repo = state["repo"]
    branch_name = state.get("branch_name", "")
    now = datetime.now(UTC).isoformat()
    decisions = state.get("decisions", [])

    def _fail(reason: str) -> dict:
        """Build a failure result with a consistent shape."""
        return preserve_extensions(
            cortex_to_dap(
                {
                    "tests_passed": False,
                    "test_output": reason,
                    # Empty structured result so the post-tester router falls into
                    # the "errors > 0" branch and treats setup failures as test
                    # failures (which they functionally are — we can't even check).
                    "tester_result": {
                        "passed": 0,
                        "failed": 0,
                        "errors": 1,
                        "failed_tests": [],
                    },
                    "current_phase": "tester_complete",
                    "error": f"tester: {reason}",
                    "__audit": {"tokens_used": 0, "cost_usd": 0.0, "section": "tester"},
                    "decisions": [
                        *decisions,
                        {
                            "node": "tester",
                            "action": "failed",
                            "reasoning": reason,
                            "backend": "",
                            "model": "",
                            "tokens": "",
                            "timestamp": now,
                        },
                    ],
                }
            ),
            original_extensions,
        )

    if not profile_exists(repo):
        raise BackendError(f"no project profile for {repo} — run `cortex init {repo}`")

    profile = load_profile(repo)
    test_cmd = profile.test_command
    if not test_cmd:
        raise BackendError(
            f"no test_command in profile for {repo} — set it via `cortex init --edit`"
        )

    workspace = repo_clone_path(repo)
    if not workspace.exists():
        return _fail(f"workspace missing: {workspace}")

    # Check out the agent's branch so we test the right code (the tester used
    # to LLM-vibe-check whatever was in `state.files_changed`, which Phase 2
    # never populated — #111).
    base_branch = "main"
    if branch_name and not checkout_agent_branch(workspace, branch_name, base_branch):
        return _fail(f"failed to checkout branch {branch_name} in workspace")

    # Capture which code we're about to test (#129). The HEAD SHA and
    # ahead-of-base count go into tester_result for diagnosability.
    # #262: we no longer short-circuit on ahead==0. Running pytest when the
    # branch has no new commits is meaningful: if tests pass the implementation
    # was already correct (verify-only task); if they fail something is broken.
    # The old short-circuit caused an infinite retry loop on verify-only runs.
    diagnostics = _collect_branch_diagnostics(workspace, base_branch)
    sha = diagnostics["sha"]
    ahead = diagnostics["ahead"]

    # Inject database URL so workspace clones (no .env) can reach PostgreSQL
    # and DB-gated tests don't skip. The tester node runs from the main
    # cortex-project dir where .env is present; we forward the URL it resolved.
    extra_env: dict[str, str] = {}
    try:
        from cortex.config.settings import load_settings as _load

        extra_env["CORTEX_DATABASE_URL"] = _load().database_url
    except Exception:
        pass

    passed, output_tail, parsed = _run_tests(workspace, test_cmd, extra_env=extra_env)

    # Embed diagnostics in the structured result so an operator querying
    # `cortex state` can see exactly which SHA was tested and how far ahead
    # of base it was. ``ahead == -1`` means the diagnostic call failed; we
    # still ran pytest, but operators should treat the count with care.
    parsed["sha"] = sha
    parsed["ahead"] = ahead

    # Reasoning includes the structured counts so audit + decision history
    # records what tester actually saw, not just a binary verdict (#89).
    reasoning = (
        f"{'PASS' if passed else 'FAIL'}: {test_cmd!r} on {branch_name} "
        f"(SHA {sha[:12] or 'unknown'}, +{ahead} ahead) — "
        f"{parsed['passed']} passed, {parsed['failed']} failed, "
        f"{parsed['errors']} errors"
    )

    return preserve_extensions(
        cortex_to_dap(
            {
                "tests_passed": passed,
                "test_output": output_tail,
                "tester_result": parsed,
                "current_phase": "tester_complete",
                "__audit": {"tokens_used": 0, "cost_usd": 0.0, "section": "tester"},
                "decisions": [
                    *decisions,
                    {
                        "node": "tester",
                        "action": "tested",
                        "reasoning": reasoning,
                        "backend": "subprocess",
                        "model": "",
                        "tokens": "",
                        "timestamp": now,
                    },
                ],
            }
        ),
        original_extensions,
    )
