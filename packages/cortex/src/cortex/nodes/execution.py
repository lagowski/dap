"""Shared execution logic for Phase 2 nodes.

Each execution node (coder, designer, documenter) follows the same pattern:
1. Filter task_assignments for tasks assigned to this agent
2. Read the issue for context
3. Create a branch
4. Call the backend with task details
5. Update the issue's Execution Status section
6. Post a completion comment
7. Log to audit tables
8. Return state updates

This module provides the shared ``run_execution_node`` function that each
thin node module calls with its agent name and system prompt.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import time
from datetime import UTC, datetime

from cortex.backends.base import BackendError, LLMRequest
from cortex.backends.registry import create_backend_with_fallback as create_backend
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.init.profile import repo_clone_path
from cortex.nodes.base import _apply_workspace_context
from cortex.state import CortexState
from cortex.templates import update_issue_section
from cortex.tools.github import (
    create_branch,
    read_issue,
)
from cortex.workspace import _default_branch, workspace_lock

logger = logging.getLogger(__name__)

# Token threshold above which we write a compact memory summary so the next
# invocation (e.g. a retry) starts with context instead of a blank slate.
_MEMORY_TOKEN_THRESHOLD = 5_000


def _build_execution_comment(
    agent_name: str,
    my_tasks: list,
    branch_name: str,
    response,
    new_commits: list[dict[str, str]],
    files_changed: list[str],
) -> str:
    """Build the completion comment body for coder/designer/documenter."""
    lines = [
        f"**{agent_name}** completed execution",
        "",
        f"Tasks: {len(my_tasks)}",
        f"Branch: `{branch_name}`",
        f"Backend: {response.backend}/{response.model}",
        f"Tokens: {response.input_tokens}in/{response.output_tokens}out",
    ]
    if files_changed:
        lines.append(f"Changed: {', '.join(files_changed)}")
    if new_commits:
        lines.append("")
        lines.append("Commits:")
        for commit in new_commits:
            sha_short = commit["sha"][:7]
            lines.append(f"- `{sha_short}` {commit['message']}")
    return "\n".join(lines)


def _save_execution_memory(
    repo: str,
    agent_name: str,
    response_content: str,
    commits: list[dict[str, str]],
    files_changed: list[str],
) -> None:
    """Write a compact session summary to the agent's persistent memory.

    Keeps retries lean: instead of the next attempt starting cold, it
    reads what was already tried. Format is 2-10 lines — short enough
    that it doesn't itself cause context bloat.

    Silently no-ops on any error so the pipeline never stalls.
    """
    try:
        from cortex.workspace import save_agent_memory

        lines: list[str] = []

        # Narration: first 8 non-empty lines from what the agent said
        if response_content:
            narration = [l for l in response_content.splitlines() if l.strip()][:8]
            lines.extend(narration)

        if commits:
            msg = commits[-1].get("message", "")[:80]
            lines.append(f"Last commit: {msg}")
        if files_changed:
            lines.append(f"Files changed: {', '.join(files_changed[:6])}")

        if not lines:
            return

        from datetime import UTC, datetime

        summary = f"## Session summary ({datetime.now(UTC).strftime('%Y-%m-%d')})\n\n" + "\n".join(
            lines
        )
        save_agent_memory(repo, agent_name, summary)
    except Exception:
        pass


# Reflog operation tokens that indicate branch history was rewritten
# rather than appended to. `git reflog show <branch>` lines that contain
# any of these strings (case-insensitive) are flagged by
# `_assert_append_only`. Kept as a module-level constant so tests can
# iterate the patterns without re-declaring them. (#185)
_REWRITE_REFLOG_PATTERNS: frozenset[str] = frozenset(
    {
        "forced-update",
        "updating ref",
        "reset: moving to",
        "rebase",
        "amend",
    }
)


def _snapshot_branch_sha(workspace, branch_name: str) -> str | None:
    """Return the current HEAD SHA of ``branch_name`` in ``workspace``.

    Captured immediately after `checkout_agent_branch` so the post-run
    reflog check can ignore reflog entries older than this point. Returns
    ``None`` on any git failure — the caller then scans the entire reflog
    instead of bounding it. (#185)
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        sha = result.stdout
        if not isinstance(sha, str):
            return None
        sha = sha.strip()
        return sha or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _read_branch_reflog(workspace, branch_name: str) -> str:
    """Return ``git reflog show <branch>`` stdout, or "" on failure. (#185)"""
    try:
        result = subprocess.run(
            ["git", "reflog", "show", branch_name],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        out = result.stdout
        return out if isinstance(out, str) else ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""


def _assert_append_only(reflog_text: str, pre_sha: str | None) -> str | None:
    """Detect branch-rewriting operations in reflog output since ``pre_sha``.

    Returns ``None`` for clean append-only history (only commit/checkout/
    fetch entries newer than ``pre_sha``) or a short error string when any
    `_REWRITE_REFLOG_PATTERNS` token appears in entries newer than
    ``pre_sha``. When ``pre_sha`` is ``None`` (e.g. snapshot failed), the
    entire reflog is scanned. (#185)
    """
    if not reflog_text:
        return None
    pre_short = pre_sha[:7] if pre_sha else None
    for raw_line in reflog_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Reflog format: "<sha> <ref>@{<n>}: <op>: <subject>"
        # Stop scanning once we reach the agent's starting state — entries
        # older than pre_sha are pre-existing history and not the agent's
        # responsibility.
        if pre_short and line.startswith(pre_short):
            return None
        lowered = line.lower()
        for pat in _REWRITE_REFLOG_PATTERNS:
            if pat in lowered:
                return f"rewrote branch history: reflog shows '{pat}'"
    return None


def _clean_workspace_for_agent(workspace) -> None:
    """Remove uncommitted changes and untracked files before running an agent.

    Phase 2 agents share a single workspace clone. When coder leaves files
    unstaged (e.g. after a failed push), those files persist in the working
    tree when the next agent (designer, documenter) checks out its own branch.
    Without this cleanup, the downstream agent's LLM sees those files, may
    stage them via ``git add .``, and commits code that was never its own
    (issue #275, related #191).

    Only safe to call immediately after ``checkout_agent_branch`` — i.e.
    before the agent has written anything.

    Ignores ``.gitignore``-d files (``git clean`` without ``-x``), so
    workspace-local config like ``.env`` is never touched.
    """
    try:
        # Unstage anything that was staged by a prior agent
        subprocess.run(
            ["git", "restore", "--staged", "."],
            cwd=str(workspace),
            capture_output=True,
            timeout=15,
        )
        # Restore modified tracked files to the checked-out branch HEAD
        subprocess.run(
            ["git", "restore", "."],
            cwd=str(workspace),
            capture_output=True,
            timeout=15,
        )
        # Remove untracked files and directories (respects .gitignore)
        result = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout.strip():
            logger.debug("Cleaned workspace before agent run: %s", result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        # Non-fatal — log and continue. Worst case: agent sees extra files and
        # the cross-task-dependency warning fires as before.
        logger.warning("_clean_workspace_for_agent failed in %s: %s", workspace, e)


def _sync_base_branch(workspace, base_branch: str) -> None:
    """Fetch base_branch so origin/<base_branch> is current.

    Ensures _collect_commits(origin/<base>..HEAD) sees only the commits
    the agent actually added, not stale history from an unsynced clone (#243).
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            timeout=60,
        )
        # Detach HEAD to origin/<base_branch> rather than hard-resetting the
        # current branch — avoids accidentally rewriting a previously
        # checked-out agent branch if the workspace is still on it.
        # `-f` discards any uncommitted changes so checkout never blocks.
        subprocess.run(
            ["git", "checkout", "-f", "--detach", f"origin/{base_branch}"],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        stderr = ""
        if isinstance(e, subprocess.CalledProcessError):
            stderr = (e.stderr or b"").decode("utf-8", errors="replace")[:200]
        logger.warning(
            "_sync_base_branch: fetch/detach %s failed: %s%s",
            base_branch,
            e,
            f" — {stderr}" if stderr else "",
        )


def checkout_agent_branch(workspace, branch_name: str, base_branch: str) -> bool:
    """Fetch + checkout an agent branch in the local workspace.

    After create_branch succeeds via the GitHub API, the LOCAL workspace clone
    is still on whatever was previously checked out (typically main). The agent
    needs to be on its own branch so commits + edits land in the right ref.

    Returns True on success, False otherwise. Logs but does not raise — the
    caller decides how to handle a failed checkout.
    """
    try:
        # Pull the new ref from origin so it's known locally
        subprocess.run(
            ["git", "fetch", "origin", branch_name],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            timeout=60,
        )
        # `-B` creates or resets the local branch; tracks origin/<branch>
        subprocess.run(
            ["git", "checkout", "-B", branch_name, f"origin/{branch_name}"],
            cwd=str(workspace),
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")[:300]
        logger.error(
            "Failed to checkout agent branch %s in %s: %s",
            branch_name,
            workspace,
            stderr,
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error(
            "Timeout while checking out branch %s in %s",
            branch_name,
            workspace,
        )
        return False


def _get_assigned_tasks(
    task_assignments: list[dict[str, str | int]], agent_name: str
) -> list[dict[str, str | int]]:
    """Filter task_assignments to find tasks for this agent."""
    return [t for t in task_assignments if str(t.get("agent", "")).lower() == agent_name]


_FILE_PATH_RE = re.compile(r"[\w./\-]+/[\w./\-]+")


def _detect_cross_task_file_references(
    task_assignments: list[dict[str, str | int]],
) -> None:
    """Warn if any task references a file path owned by a different agent.

    Phase 2 agents each branch from main and cannot see each other's commits.
    If Task B mentions a file that Task A is supposed to create, the documenter
    (or whichever dependent agent) will have nothing to work with and will
    produce an empty or hallucinated result.

    Non-blocking — emits a WARNING and returns. The dispatcher prompt
    (agents.yaml) is the primary fix; this is a runtime safety net.
    """
    # Build a map: file_path -> agent that owns (mentions it first by priority)
    file_owners: dict[str, str] = {}
    for assignment in task_assignments:
        agent = str(assignment.get("agent", "")).lower()
        task = str(assignment.get("task", ""))
        for path in _FILE_PATH_RE.findall(task):
            if path not in file_owners:
                file_owners[path] = agent

    # Check each task: does it reference a file owned by a different agent?
    for assignment in task_assignments:
        agent = str(assignment.get("agent", "")).lower()
        task = str(assignment.get("task", ""))
        for path in _FILE_PATH_RE.findall(task):
            owner = file_owners.get(path)
            if owner and owner != agent:
                logger.warning(
                    "cross-task dependency detected: agent '%s' references "
                    "'%s' owned by agent '%s' — %s cannot see %s's commits; "
                    "consider consolidating both tasks under one agent",
                    agent,
                    path,
                    owner,
                    agent,
                    owner,
                )


def _collect_commits(
    workspace, branch_name: str, base_branch: str
) -> tuple[list[dict[str, str]], list[str]]:
    """Read commits + files-changed from the workspace for ``base..HEAD``.

    Returns ``([{"sha","message"}], [paths])``. Both empty if nothing changed
    or if git commands fail (logged, never raised). Phase 3 nodes (tester,
    pr_creator) need this ground truth — without it they see empty state and
    hallucinate (issue #111) or open empty PRs (issue #110).
    """
    commits: list[dict[str, str]] = []
    files_changed: list[str] = []
    rev_range = f"origin/{base_branch}..HEAD"

    try:
        log = subprocess.run(
            ["git", "log", "--format=%H::%s", rev_range],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        for line in log.stdout.splitlines():
            sha, _, msg = line.partition("::")
            if sha:
                commits.append({"sha": sha, "message": msg})
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        logger.warning("git log %s failed in %s: %s", rev_range, workspace, stderr[:200])

    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", rev_range],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        files_changed = [f for f in diff.stdout.splitlines() if f]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        logger.warning("git diff %s failed in %s: %s", rev_range, workspace, stderr[:200])

    return commits, files_changed


def _is_ancestor(workspace, ref: str, head: str = "HEAD") -> bool | None:
    """Return True if ``ref`` is an ancestor of ``head``, False if not, None on error.

    Uses ``git merge-base --is-ancestor ref head``. Returns ``None`` when
    ``ref`` does not exist on origin or git fails — the caller treats ``None``
    as "unable to determine; allow the operation". (#219)
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ref, head],
            cwd=str(workspace),
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _push_branch(workspace, repo: str, branch_name: str, token: str) -> str:
    """Push ``branch_name`` from the workspace to origin. Returns "" or an error.

    Uses the GH_TOKEN_CODE token directly in a one-shot URL rather than the
    workspace's stored remote, so we don't depend on a credential helper or
    bake the token into the clone (issue #110).

    Pre-push ancestry check: if ``origin/<branch>`` exists and its tip is not
    an ancestor of the local HEAD, the push is aborted — it would be a
    force-push that could wipe operator out-of-band commits (#219).
    """
    if not token:
        return "no GH_TOKEN_CODE configured"

    # Guard: abort if origin/<branch> exists and is not an ancestor of HEAD.
    # Returns None when the origin ref is absent (new branch) or git fails —
    # only abort on an explicit False (diverged history).
    origin_ref = f"origin/{branch_name}"
    is_anc = _is_ancestor(workspace, origin_ref)
    if is_anc is False:
        return (
            f"ancestry check failed: {origin_ref} is not an ancestor of HEAD — "
            f"push would force-push and wipe operator commits (#219)"
        )

    auth_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    try:
        subprocess.run(
            ["git", "push", auth_url, branch_name],
            cwd=str(workspace),
            capture_output=True,
            timeout=60,
            check=True,
        )
        return ""
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")
        # Don't leak the token if git echoes the URL back in an error
        return stderr.replace(token, "<redacted>")[:300]
    except subprocess.TimeoutExpired:
        return "push timed out after 60s"


def _format_retry_context(state: CortexState) -> str:
    """Format failed-test feedback for prepending to coder's prompt (#89).

    Returns an empty string when this isn't a retry (no prior tester run,
    or tester reported a clean pass). On retry, returns a markdown block
    naming the failed tests + a directive to target them. The block is
    deliberately terse — pytest's stdout tail is already in audit if more
    is needed; the agent doesn't need a thousand lines repeated here.
    """
    retry_count = int(state.get("pr_merger_retry_count", 0) or 0)
    if retry_count <= 0:
        return ""

    tester_result = state.get("tester_result") or {}
    failed_tests = tester_result.get("failed_tests") or []
    failed = int(tester_result.get("failed", 0) or 0)
    errors = int(tester_result.get("errors", 0) or 0)

    if failed == 0 and errors == 0 and not failed_tests:
        # Defensive: retry counter set but nothing to feed back. Just note
        # this is a retry attempt without details.
        return (
            f"## Retry attempt {retry_count}\n"
            f"Previous tester run reported a failure but produced no "
            f"structured details. Re-examine the assigned tasks.\n\n"
        )

    lines = [
        f"## Retry attempt {retry_count}",
        (
            f"The previous attempt's tests failed: "
            f"{failed} failure(s), {errors} error(s). "
            f"Address the specific failures below before re-running tests."
        ),
        "",
    ]
    if failed_tests:
        lines.append("Failed tests:")
        for test in failed_tests[:20]:  # cap to avoid token blowups on huge fanouts
            lines.append(f"- `{test}`")
        if len(failed_tests) > 20:
            lines.append(f"- ...and {len(failed_tests) - 20} more")
    return "\n".join(lines) + "\n\n"


_MAX_TURNS_CAP = 100
_TIMEOUT_SEC_CAP = 1800  # 30 min — matches Claude CLI's practical ceiling


def _apply_task_budgets(agent_config: dict, my_tasks: list[dict[str, str | int]]) -> dict:
    """Override agent_config max_turns/timeout_sec from per-task budgets (#49, #133).

    Execution agents pick up batched tasks one at a time within a single
    backend invocation, so the budget must cover the *wall-clock for all of
    them* — i.e. the sum, not the max. (#117 attempt 3 dogfood signature:
    coder got max(180,300,180)=300s for 3 sequential tasks needing ~660s
    and timed out at 300s, run 83524afe-983f-4902-b84f-f95d75645e19.)

    Sums are capped (max_turns ≤ 100, timeout_sec ≤ 1800s) to bound the
    blast radius of a malformed dispatcher emission (e.g. 15 small tasks
    would otherwise yield 2700s).

    Returns a (possibly copied) agent_config; never mutates the input.
    """
    int_budget = lambda key: [  # noqa: E731
        int(t[key]) for t in my_tasks if isinstance(t.get(key), int)
    ]
    max_turns_values = int_budget("max_turns")
    timeout_values = int_budget("timeout_sec")
    if not max_turns_values and not timeout_values:
        return agent_config
    overridden = dict(agent_config)
    if max_turns_values:
        overridden["max_turns"] = min(sum(max_turns_values), _MAX_TURNS_CAP)
    if timeout_values:
        overridden["timeout_sec"] = min(sum(timeout_values), _TIMEOUT_SEC_CAP)
    return overridden


def run_execution_node(
    state: CortexState,
    agent_name: str,
    system_prompt: str,
) -> dict:
    """Execute tasks assigned to a specific agent.

    Args:
        state: Current pipeline state.
        agent_name: Agent name as in agents.yaml (coder, designer, documenter).
        system_prompt: System prompt for the LLM backend.

    Returns:
        State update dict with branch_name, decisions appended.
    """
    task_assignments = state.get("task_assignments", [])
    my_tasks = _get_assigned_tasks(task_assignments, agent_name)

    # Nothing assigned to us — return state unchanged
    if not my_tasks:
        return {}

    _detect_cross_task_file_references(task_assignments)

    settings = load_settings()
    token = settings.get_github_token("code")
    repo = state["repo"]
    issue_number = state["issue_number"]

    # 1. Read the issue for context
    issue_data = read_issue.invoke({"repo": repo, "issue_number": issue_number, "token": token})
    if "error" in issue_data:
        return {
            "error": f"{agent_name}: failed to read issue: {issue_data['error']}",
            "decisions": [
                *state.get("decisions", []),
                {
                    "node": agent_name,
                    "action": "error",
                    "reasoning": issue_data["error"],
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            ],
        }

    # 2. Create branch — fail loud on permission errors (issue #43)
    branch_name = f"cortex/issue-{issue_number}/{agent_name}"
    # On retry (retry_count >= 1), coder must build on the existing branch tip
    # instead of resetting to default — otherwise create_branch force-resets the
    # branch and wipes prior commits. Designer/documenter chain from the
    # previous agent via state["branch_name"]. (#219)
    # Use origin/HEAD to determine the repo's default branch (e.g. "develop"
    # for repos where "main" is not the primary branch).
    _workspace_for_branch = repo_clone_path(repo)
    _repo_default = (
        _default_branch(_workspace_for_branch) if _workspace_for_branch.exists() else "main"
    )
    retry_count = int(state.get("retry_count", 0) or 0)
    if agent_name == "coder" and retry_count >= 1:
        base_branch = branch_name  # preserve existing commits on retry
    elif agent_name == "coder":
        base_branch = _repo_default
    else:
        base_branch = state.get("branch_name", _repo_default)
    branch_result = create_branch.invoke(
        {"repo": repo, "branch_name": branch_name, "base": base_branch, "token": token}
    )
    branch_failed = isinstance(branch_result, str) and branch_result.startswith("error:")
    if branch_failed:
        logger.error(
            "Agent %s failed to create branch %s (token role=code): %s",
            agent_name,
            branch_name,
            branch_result,
        )
        err_msg = f"{agent_name}: branch creation failed: {branch_result}"
        return {
            "branch_name": branch_name,
            "current_phase": "execution",
            "commits": [],
            "files_changed": [],
            "decisions": [
                *state.get("decisions", []),
                {
                    "node": agent_name,
                    "action": "failed (branch creation 403)",
                    "reasoning": str(branch_result),
                    "backend": "",
                    "model": "",
                    "tokens": "",
                    "cost_usd": "0",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            ],
            "error": err_msg,
        }

    # 2b. Checkout the agent branch locally so claude_cli/opencode_cli
    # operate on the right ref (issue #60). Without this, agents work on
    # whatever was checked out before (typically main) and refuse to commit.
    workspace = repo_clone_path(repo)
    # Serialize all workspace-writing ops under a per-repo file lock (#255).
    # Prevents index.lock conflicts when concurrent coder invocations race on
    # the shared workspace clone. nullcontext when no workspace ops will happen
    # (branch_failed or workspace absent).
    _ws_lock = (
        workspace_lock(repo)
        if not branch_failed and workspace.exists()
        else contextlib.nullcontext()
    )
    with _ws_lock:
        pre_sha: str | None = None
        if not branch_failed and workspace.exists():
            # Sync base branch before checkout: updates origin/<base_branch> so
            # _collect_commits(origin/<base>..HEAD) is accurate (#243). Also
            # resets workspace to base HEAD so a stale clone can't produce a
            # 300-file diff. Only for coder first-run; designer/documenter chain
            # from coder's branch, retry-coder builds on existing branch tip.
            if agent_name == "coder" and retry_count == 0:
                _sync_base_branch(workspace, base_branch)
            checkout_agent_branch(workspace, branch_name, base_branch)
            # Remove untracked / uncommitted files left by previous agents
            # so this agent cannot accidentally stage them (#275).
            _clean_workspace_for_agent(workspace)
            # Snapshot the pre-invocation HEAD so the post-run reflog check
            # can ignore pre-existing history (#185).
            pre_sha = _snapshot_branch_sha(workspace, branch_name)

        state.get("run_id", "")

        # 3. Build prompt from assigned tasks
        task_descriptions = "\n".join(
            f"- [{t.get('priority', 'normal')}] {t.get('task', 'unnamed task')}" for t in my_tasks
        )

        # Retry context (#89): if this invocation is a retry after tester failed,
        # prepend the failed test names so coder can target what broke instead of
        # re-running the same plan blindly.
        retry_context = _format_retry_context(state)

        user_prompt = (
            f"Issue #{issue_number}: {issue_data.get('title', state.get('issue_title', ''))}\n\n"
            f"{retry_context}"
            f"Issue body:\n{issue_data.get('body', state.get('issue_body', ''))}\n\n"
            f"Assigned tasks:\n{task_descriptions}\n\n"
            f"Branch: {branch_name}\n"
            f"Repository: {repo}"
        )

        # 4. Call backend — inject the per-repo workspace as cwd so tool-using
        # backends (claude_cli/opencode_cli) edit files in the right directory
        # (issue #66). Without this, they fall back to os.getcwd() — typically
        # the dev checkout — and commits land in the framework repo instead of
        # the agent's workspace clone.
        agent_config = get_agent_backend_config(agent_name)
        agent_config, user_prompt = _apply_workspace_context(
            agent_config,
            agent_name,
            repo,
            user_prompt,
        )
        # Per-task budgets (#49): override agent_config max_turns/timeout_sec
        # from values the dispatcher attached to this task. Falls through to the
        # static agents.yaml defaults when the assignment has no budget fields.
        agent_config = _apply_task_budgets(agent_config, my_tasks)
        backend = create_backend(agent_config)
        resolved_system_prompt = agent_config.get("system_prompt", system_prompt)

        start = time.monotonic()
        try:
            response = backend.invoke(
                LLMRequest(
                    system_prompt=resolved_system_prompt,
                    user_prompt=user_prompt,
                    temperature=agent_config.get("temperature", 0.2),
                    max_tokens=agent_config.get("max_tokens", 4000),
                )
            )
        except BackendError as e:
            # #128: backend timeout / unavailability is a hard failure. Don't
            # commit/push, don't touch the issue body, don't post a "completed"
            # comment. Audit the failure shape so `cortex audit` shows it, set
            # state.error so the human gate before tester surfaces it, return
            # early with empty commits/files_changed so Phase 3 sees ground truth.
            duration_ms = int((time.monotonic() - start) * 1000)
            err_msg = str(e) or e.__class__.__name__
            backend_name = getattr(e, "backend", "") or agent_config.get("backend", "")
            logger.error(
                "Agent %s backend invocation failed (%s): %s",
                agent_name,
                backend_name,
                err_msg,
            )
            return {
                "branch_name": branch_name,
                "current_phase": "execution",
                "commits": [],
                "files_changed": [],
                "error": f"{agent_name}: {err_msg}",
                "__audit": {
                    "tokens_used": 0,
                    "cost_usd": 0.0,
                    "section": agent_name,
                },
                "decisions": [
                    *state.get("decisions", []),
                    {
                        "node": agent_name,
                        "action": "failed (backend error)",
                        "reasoning": err_msg[:200],
                        "backend": backend_name,
                        "model": "",
                        "tokens": "0in/0out",
                        "cost_usd": "0.0",
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                ],
            }
        duration_ms = int((time.monotonic() - start) * 1000)

        # 5b. Collect commits + files-changed from the workspace and push to origin
        # so Phase 3 nodes (tester, pr_creator) see ground truth (#110, #111).
        # Skipped if the workspace doesn't exist (mocked tests, fresh CI runs) or
        # the branch creation failed (no ref to push).
        new_commits: list[dict[str, str]] = []
        files_changed: list[str] = []
        push_error: str = ""
        rewrite_error: str | None = None
        if not branch_failed and workspace.exists():
            # Defensive: detect history-rewriting operations (force-push,
            # reset --hard, rebase, amend) the agent may have done despite the
            # prompt forbidding them. If the branch was rewritten, an operator's
            # out-of-band commit pushed between rounds would be silently wiped
            # — abort before pushing instead. (#185)
            reflog_text = _read_branch_reflog(workspace, branch_name)
            rewrite_error = _assert_append_only(reflog_text, pre_sha)
            if rewrite_error:
                logger.error(
                    "Agent %s rewrote branch %s history: %s",
                    agent_name,
                    branch_name,
                    rewrite_error,
                )
            else:
                new_commits, files_changed = _collect_commits(workspace, branch_name, base_branch)
                if new_commits:
                    push_error = _push_branch(workspace, repo, branch_name, token)
                    if push_error:
                        logger.error(
                            "Push failed for %s in %s: %s",
                            branch_name,
                            repo,
                            push_error,
                        )

    # 5c. Persist a compact session summary to agent memory so retries start
    # lean. Saved after commits/files are known so the summary is complete.
    _save_execution_memory(repo, agent_name, response.content, new_commits, files_changed)

    # 6. Update Execution Status section in the issue body (delegated to DAP step)
    current_body = issue_data.get("body", "")
    status_line = (
        f"**{agent_name}**: completed "
        f"({len(my_tasks)} task{'s' if len(my_tasks) != 1 else ''}) "
        f"on branch `{branch_name}` "
        f"at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    existing_section = ""
    # Preserve existing status lines from other agents
    section_match = re.search(r"## Execution Status\s*\n(.*?)(?=\n## |\Z)", current_body, re.DOTALL)
    if section_match:
        existing_section = section_match.group(1).strip()

    new_section = f"{existing_section}\n{status_line}".strip()
    updated_body = update_issue_section(current_body, "Execution Status", new_section)

    # 7. Build completion comment
    comment_body = _build_execution_comment(
        agent_name, my_tasks, branch_name, response, new_commits, files_changed
    )

    # Delegate GitHub writes + audit logging to DAP step (#224)
    from cortex.dap_steps.execution_write import run_side_effects as _exec_write

    write_result = _exec_write(
        state,
        {},  # audit dict not needed by execution_write — it uses explicit params
        agent_name,
        token=token,
        status_line=status_line,
        existing_section=existing_section,
        updated_body=updated_body,
        comment_body=comment_body,
    )
    update_failed = "error" in write_result

    # 8. Return state updates
    action_label = "executed"
    if branch_failed or update_failed or push_error or rewrite_error:
        action_label = "executed (with GitHub errors)"

    decision_entry = {
        "node": agent_name,
        "action": action_label,
        "reasoning": f"{len(my_tasks)} tasks on branch {branch_name}",
        "backend": response.backend,
        "model": response.model,
        "tokens": f"{response.input_tokens}in/{response.output_tokens}out",
        "cost_usd": str(response.cost_usd),
        "timestamp": datetime.now(UTC).isoformat(),
    }

    result: dict = {
        "branch_name": branch_name,
        "current_phase": "execution",
        "commits": new_commits,
        "files_changed": files_changed,
        "decisions": [*state.get("decisions", []), decision_entry],
        "__audit": {
            "tokens_used": ((response.input_tokens or 0) + (response.output_tokens or 0)),
            "cost_usd": response.cost_usd,
            "section": agent_name,
            "backend": response.backend,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "duration_ms": duration_ms,
            "branch_name": branch_name,
            "commits_pushed": len(new_commits),
        },
    }
    # Track coder's branch name separately so downstream nodes can always
    # find it even after designer/documenter overwrite branch_name (#161).
    if agent_name == "coder":
        result["coder_branch_name"] = branch_name
    if branch_failed:
        result["error"] = f"{agent_name}: branch creation failed: {branch_result}"
    elif rewrite_error:
        # #185: silent data-loss guard. Out-of-band commits operators push
        # between rounds get wiped if the agent force-pushes/rebases.
        result["error"] = (
            f"{agent_name}: rewrote branch history (force-push or reset detected) — aborting"
        )
    elif update_failed:
        result["error"] = (
            f"{agent_name}: update Execution Status failed: {write_result.get('error', '?')}"
        )
    elif push_error:
        # Surface push failures as state.error so Phase 3 doesn't proceed
        # against an unpushed branch and pr_creator silently fail (#110).
        result["error"] = f"{agent_name}: push failed: {push_error}"
    return result
