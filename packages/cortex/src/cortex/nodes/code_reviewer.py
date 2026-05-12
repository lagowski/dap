"""Phase 2.5 code review node — pre-merge diff checker (#152).

Sits between documenter and tester. Reads git diff from the workspace clone,
applies regex checks, and returns approve (proceed to tester) or needs_work
(route to retry_prepare for a bounded coder retry).

This node does NOT call any LLM. It is a pure Python regex checker.
On any error (missing state, git failure, exception) it approves and
continues — never block the pipeline on a checker that cannot run.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex
from cortex.init.profile import repo_clone_path

if TYPE_CHECKING:
    from cortex.state import CortexState

__all__ = ["run"]

# ---------------------------------------------------------------------------
# Regex patterns for added lines only.
# "Added line" = starts with "+" but NOT "+++" (the diff header).
# ---------------------------------------------------------------------------

# Hardcoded secrets: assignment of a quoted string of at least 8 chars.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'password\s*=\s*[\'"][^\'"]{8,}[\'"]', re.IGNORECASE),
    re.compile(r'api_key\s*=\s*[\'"][^\'"]{8,}[\'"]', re.IGNORECASE),
    re.compile(r'secret\s*=\s*[\'"][^\'"]{8,}[\'"]', re.IGNORECASE),
    re.compile(r'token\s*=\s*[\'"][^\'"]{8,}[\'"]', re.IGNORECASE),
]

# Debug print/log statements.
_DEBUG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bprint\("),
    re.compile(r"\bconsole\.log\("),
]

# TODO / FIXME / XXX comments.
_TODO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"#\s*TODO\b", re.IGNORECASE),
    re.compile(r"#\s*FIXME\b", re.IGNORECASE),
    re.compile(r"#\s*XXX\b", re.IGNORECASE),
    re.compile(r"//\s*TODO\b", re.IGNORECASE),
    re.compile(r"//\s*FIXME\b", re.IGNORECASE),
]


def _is_added_line(line: str) -> bool:
    """Return True iff this diff line represents an addition (not a header)."""
    return line.startswith("+") and not line.startswith("+++")


def _check_secrets(line: str, lineno: int, findings: list[str]) -> None:
    """Append a finding if the added line contains a hardcoded secret pattern."""
    content = line[1:]  # strip the leading "+"
    for pattern in _SECRET_PATTERNS:
        m = pattern.search(content)
        if m:
            findings.append(f"hardcoded secret at line {lineno}: {content.strip()[:80]}")
            return  # one finding per line is enough


def _check_debug(line: str, lineno: int, findings: list[str]) -> None:
    """Append a finding if the added line contains a debug print/log."""
    content = line[1:]
    for pattern in _DEBUG_PATTERNS:
        if pattern.search(content):
            findings.append(f"debug print at line {lineno}: {content.strip()[:80]}")
            return


def _check_todo(line: str, lineno: int, findings: list[str]) -> None:
    """Append a finding if the added line contains a TODO/FIXME/XXX comment."""
    content = line[1:]
    for pattern in _TODO_PATTERNS:
        if pattern.search(content):
            findings.append(f"TODO/FIXME/XXX comment at line {lineno}: {content.strip()[:80]}")
            return


def _check_untested_api(diff: str, files_changed: list[str]) -> list[str]:
    """Heuristic: flag new public `def ` lines when no test file was touched.

    This is intentionally coarse. If ANY test file appears in files_changed
    we consider tests present and skip the check. We only flag when:
      - at least one added line matches `def <name>(` (new function)
      - AND no file in files_changed matches `test_` or `_test`
    """
    has_new_def = any(
        re.search(r"^\+\s*def\s+\w+\(", line)
        for line in diff.splitlines()
        if _is_added_line(line) and not line.startswith("+++")
    )
    if not has_new_def:
        return []

    test_file_present = any(("test_" in f or "_test" in f) for f in (files_changed or []))
    if test_file_present:
        return []

    return [
        "public API addition without test file change: "
        "new def found but no test_* / *_test file in files_changed"
    ]


def _run_git_diff(workspace: str, branch: str) -> str | None:
    """Run `git diff origin/main...HEAD` in *workspace*.

    Falls back to `git diff main...HEAD` if origin/main fails.
    Returns the diff string on success, None on any failure.
    """
    for base in ("origin/main", "main"):
        try:
            result = subprocess.run(
                ["git", "diff", f"{base}...HEAD"],
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            continue
    return None


def _get_changed_files(workspace: str, branch: str) -> list[str] | None:
    """Return the list of files changed on *branch* relative to origin/main.

    Uses three-dot form `origin/main...<branch>` to get the full changed-file
    list since divergence regardless of merge state. NOTE: this is the
    intentionally different sibling of `cortex bp`'s two-dot squash-merge
    detection in `cortex/cli.py` — three-dot here, two-dot there. The two
    regressions are independent; do not "unify" them in a future consistency
    pass without re-deriving the semantics.

    Returns None on any failure (subprocess error, missing workspace, etc.)
    so the caller can fail open and skip the heuristic — matching the
    existing `_approved_result(reason="git diff failed")` behavior.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"origin/main...{branch}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return result.stdout.splitlines()
    except Exception:
        return None


def _with_extensions(
    delta: dict, original_extensions: dict, review_attempts: int, audit: dict
) -> dict:
    """Merge original_extensions + review_attempts into a cortex_to_dap delta.

    Also re-attaches ``__audit`` (stripped by cortex_to_dap) so the DAP
    python-func adapter can record tokens/cost.

    Prevents LangGraph's dict-replace behaviour from clobbering extensions
    fields that the code_reviewer didn't write (e.g. task_assignments,
    issue_number).  See cortex-project#303.
    """
    merged = {
        **original_extensions,
        **delta.get("extensions", {}),
        "review_attempts": review_attempts,
    }
    delta["extensions"] = merged
    delta["__audit"] = audit
    return delta


async def run(state: dict, config: dict) -> dict:
    """Phase 2.5: deterministic diff checker — no LLM, never blocks on error.

    Reads the git diff from the workspace, applies four regex checks on
    added lines only, and returns:

        {"review_findings": [...], "review_approved": bool}

    ``review_approved`` is ``True`` when ``review_findings`` is empty.

    On any failure (missing state, git error, exception) the node returns
    approved so the pipeline is never blocked by checker unavailability.
    """
    # Preserve all existing extensions before conversion so they survive the
    # LangGraph state-delta merge (which replaces the extensions dict rather
    # than merging it).  See cortex-project#303.
    original_extensions: dict = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    timestamp = datetime.now(UTC).isoformat()
    findings: list[str] = []
    # Read current retry count so we can increment it on needs_work.
    current_attempts: int = int(state.get("review_attempts") or 0)

    try:
        repo = state.get("repo", "")
        branch = (state.get("branch_name") or "").strip()

        if not repo or not branch:
            # Missing state — approve and continue.
            return _with_extensions(
                cortex_to_dap(
                    _approved_result(state, findings, timestamp, reason="missing repo/branch_name")
                ),
                original_extensions,
                current_attempts,
                {"tokens_used": 0, "cost_usd": 0.0, "section": "code_reviewer"},
            )

        workspace = str(repo_clone_path(repo))
        if not workspace:
            return _with_extensions(
                cortex_to_dap(
                    _approved_result(state, findings, timestamp, reason="missing workspace")
                ),
                original_extensions,
                current_attempts,
                {"tokens_used": 0, "cost_usd": 0.0, "section": "code_reviewer"},
            )

        diff = _run_git_diff(workspace, branch)
        if diff is None:
            # git failed — approve and continue.
            return _with_extensions(
                cortex_to_dap(
                    _approved_result(state, findings, timestamp, reason="git diff failed")
                ),
                original_extensions,
                current_attempts,
                {"tokens_used": 0, "cost_usd": 0.0, "section": "code_reviewer"},
            )

        lines = diff.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not _is_added_line(line):
                continue
            _check_secrets(line, lineno, findings)
            _check_debug(line, lineno, findings)
            _check_todo(line, lineno, findings)

        files_changed = _get_changed_files(workspace, branch)
        if files_changed is not None:
            findings.extend(_check_untested_api(diff, files_changed))

    except Exception:
        # Never block the pipeline on reviewer errors.
        findings = []

    # Filter out findings the operator has already acknowledged via a gate
    # approval (#244). An acknowledged finding was present in review_findings
    # when the operator hit `cortex approve`; the cli persists it into
    # acknowledged_findings so subsequent passes don't re-block on it.
    acknowledged = set(state.get("acknowledged_findings") or [])
    effective_findings = [f for f in findings if f not in acknowledged]

    approved = len(effective_findings) == 0
    action = "approved" if approved else "needs_work"
    reasoning = (
        "diff clean — no issues found"
        if approved
        else f"{len(effective_findings)} issue(s): {'; '.join(effective_findings[:3])}"
    )

    decisions = [
        *list(state.get("decisions") or []),
        {
            "node": "code_reviewer",
            "action": action,
            "reasoning": reasoning,
            "backend": "",
            "model": "",
            "tokens": "",
            "timestamp": timestamp,
        },
    ]

    new_attempts = current_attempts + (0 if approved else 1)

    _audit = {"tokens_used": 0, "cost_usd": 0.0, "section": "code_reviewer"}
    return _with_extensions(
        cortex_to_dap(
            {
                "review_findings": effective_findings,
                "review_approved": approved,
                "review_attempts": new_attempts,
                "current_phase": "code_reviewer",
                "decisions": decisions,
            }
        ),
        original_extensions,
        new_attempts,
        _audit,
    )


def _approved_result(state: CortexState, findings: list[str], timestamp: str, reason: str) -> dict:
    """Return an approved result with an audit decision explaining why."""
    decisions = [
        *list(state.get("decisions") or []),
        {
            "node": "code_reviewer",
            "action": "approved",
            "reasoning": f"skipped ({reason}) — approving by default",
            "backend": "",
            "model": "",
            "tokens": "",
            "timestamp": timestamp,
        },
    ]
    return {
        "review_findings": [],
        "review_approved": True,
        "current_phase": "code_reviewer",
        "decisions": decisions,
        "__audit": {"tokens_used": 0, "cost_usd": 0.0, "section": "code_reviewer"},
    }
