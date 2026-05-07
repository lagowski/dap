"""Per-project workspace + agent memory.

Each agent gets its own CLAUDE.md inside the cloned repo's `.cortex/agents/`
directory. claude_cli/opencode_cli backends run with cwd set to the workspace
and pick up the composed CLAUDE.md natively. Ollama agents get the same
content injected into their prompts.

Workspace layout::

    ~/.cortex/projects/<owner>-<name>/repo/
    ├── .cortex/
    │   ├── context.md              # Shared project context (rendered profile)
    │   └── agents/
    │       ├── mockup/CLAUDE.md
    │       ├── specify/CLAUDE.md
    │       └── ...one dir per agent
    ├── CLAUDE.md                   # Project's own (untouched)
    └── ...source code...
"""

from __future__ import annotations

import contextlib
import fcntl
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from cortex.init.profile import load_profile, repo_clone_path

# Canonical list of all 12 agents wired into the graph.
# Stays in sync with cortex/config/agents.yaml.
AGENT_NAMES: tuple[str, ...] = (
    # Phase 1 — enrichment
    "mockup",
    "specify",
    "cicd",
    "dispatcher",
    "finalize",
    # Phase 2 — execution
    "coder",
    "designer",
    "documenter",
    # Phase 3 — merge & verify
    "tester",
    "pr_creator",
    "reviewer",
    "pr_merger",
)


def workspace_dot_cortex(repo: str) -> Path:
    """Return the .cortex/ directory inside the cloned repo."""
    return repo_clone_path(repo) / ".cortex"


def workspace_context_path(repo: str) -> Path:
    """Return path to the shared project context file."""
    return workspace_dot_cortex(repo) / "context.md"


def agent_memory_dir(repo: str, agent: str, project_id: str = "") -> Path:
    """Return the directory for a specific agent's memory.

    When ``project_id`` is provided the path is scoped under
    ``.cortex/agents/<project_id>/<agent>/`` for multi-project isolation.
    """
    if agent not in AGENT_NAMES:
        raise ValueError(f"Unknown agent: {agent!r}. Known: {AGENT_NAMES}")
    if project_id:
        return workspace_dot_cortex(repo) / "agents" / project_id / agent
    return workspace_dot_cortex(repo) / "agents" / agent


def agent_memory_path(repo: str, agent: str, project_id: str = "") -> Path:
    """Return path to a specific agent's CLAUDE.md memory file."""
    return agent_memory_dir(repo, agent, project_id) / "CLAUDE.md"


def init_workspace(repo: str, project_id: str = "") -> Path:
    """Create the `.cortex/` workspace structure inside the cloned repo.

    Idempotent — won't overwrite existing memory files.
    Returns the `.cortex/` directory path.
    """
    dot_cortex = workspace_dot_cortex(repo)
    dot_cortex.mkdir(parents=True, exist_ok=True)

    # Shared context file (only initialise if missing — let cortex init
    # populate it from the profile)
    ctx = workspace_context_path(repo)
    if not ctx.exists():
        _write_initial_context(repo, ctx)

    # One directory + CLAUDE.md per agent
    for agent in AGENT_NAMES:
        d = agent_memory_dir(repo, agent, project_id)
        d.mkdir(parents=True, exist_ok=True)
        memory_file = d / "CLAUDE.md"
        if not memory_file.exists():
            memory_file.write_text(_initial_agent_memory(agent))

    return dot_cortex


def _write_initial_context(repo: str, path: Path) -> None:
    """Render the project profile (if any) into a shared context document."""
    profile = load_profile(repo)
    if profile is None:
        path.write_text(
            f"# Cortex Project Context\n\n"
            f"No profile yet. Run `cortex init {repo}` to generate one.\n"
        )
        return

    parts = [
        "# Cortex Project Context",
        "",
        f"**Repository:** {profile.repo}",
        f"**Language:** {profile.language}",
    ]
    if profile.framework:
        parts.append(f"**Framework:** {profile.framework}")
    if profile.package_manager:
        parts.append(f"**Package manager:** {profile.package_manager}")
    if profile.test_command:
        parts.append(f"**Test command:** `{profile.test_command}`")
    if profile.build_command:
        parts.append(f"**Build command:** `{profile.build_command}`")
    if profile.entry_point:
        parts.append(f"**Entry point:** `{profile.entry_point}`")
    if profile.key_directories:
        parts.append(f"**Key directories:** {', '.join(profile.key_directories)}")
    if profile.readme_summary:
        parts.extend(["", "## README summary", "", profile.readme_summary])

    path.write_text("\n".join(parts) + "\n")


def _initial_agent_memory(agent: str) -> str:
    """Initial content for a fresh agent CLAUDE.md."""
    return (
        f"# {agent} agent — project memory\n\n"
        f"This file is updated as the {agent} agent learns about this project "
        f"across runs. Initially empty.\n"
    )


def load_agent_memory(repo: str, agent: str, project_id: str = "") -> str:
    """Read the agent's CLAUDE.md. Returns empty string if missing."""
    path = agent_memory_path(repo, agent, project_id)
    if not path.exists():
        return ""
    return path.read_text()


def save_agent_memory(repo: str, agent: str, content: str, project_id: str = "") -> Path:
    """Write the agent's CLAUDE.md, creating parents as needed."""
    path = agent_memory_path(repo, agent, project_id)  # raises ValueError on unknown agent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def compose_claude_md(repo: str, agent: str) -> str:
    """Merge project + shared context + agent memory into a single document.

    Order:
      1. Project's own CLAUDE.md (if any)
      2. .cortex/context.md (shared project context)
      3. .cortex/agents/<agent>/CLAUDE.md (agent's persistent memory)

    Returns empty string if the workspace doesn't exist at all.
    """
    repo_path = repo_clone_path(repo)
    if not repo_path.exists():
        return ""

    sections: list[str] = []

    # 1. Project's own CLAUDE.md (untouched by Cortex)
    project_claude = repo_path / "CLAUDE.md"
    if project_claude.exists():
        sections.append("# === Project CLAUDE.md ===\n\n" + project_claude.read_text())

    # 2. Shared context (project profile rendered)
    ctx = workspace_context_path(repo)
    if ctx.exists():
        sections.append("# === Shared Project Context ===\n\n" + ctx.read_text())

    # 3. Agent-specific memory
    if agent in AGENT_NAMES:
        agent_memory = load_agent_memory(repo, agent)
        if agent_memory:
            sections.append(f"# === {agent} agent memory ===\n\n" + agent_memory)

    return "\n\n".join(sections)


@contextlib.contextmanager
def workspace_lock(repo: str):
    """Exclusive per-repo file lock around workspace-mutating git operations.

    Prevents ``index.lock`` conflicts when multiple concurrent pipeline runs
    target the same repo's shared workspace clone.

    Auto-removes a stale ``.git/index.lock`` on lock acquisition so a
    previously crashed git process doesn't block the next invocation.

    Falls back to a no-op (yields immediately) when the profile directory
    doesn't exist — the workspace hasn't been cloned yet and there is nothing
    to protect.

    Phase 1 agents (read-only) do NOT need this lock.
    """
    lock_path = repo_clone_path(repo).parent / "workspace.lock"
    if not lock_path.parent.exists():
        yield
        return

    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            stale = repo_clone_path(repo) / ".git" / "index.lock"
            if stale.exists():
                stale.unlink()
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def list_workspaces(profiles_dir: Path, db_url: str) -> list[dict[str, Any]]:
    """Return one dict per registered workspace with last-run timestamp.

    Each dict has keys: ``workspace`` (dir name), ``repo`` (owner/name),
    ``last_run`` (ISO-8601 string or ``None``).

    Pure function: accepts explicit paths and DB URL for test isolation.
    """
    if not profiles_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for profile_file in sorted(profiles_dir.glob("*/profile.yaml")):
        workspace_name = profile_file.parent.name
        try:
            with profile_file.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            repo = data.get("repo", "")
        except Exception:
            repo = ""
        entries.append({"workspace": workspace_name, "repo": repo, "last_run": None})

    if not entries:
        return []

    # Query DB for last-run timestamps
    import psycopg

    try:
        with psycopg.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT repo, MAX(started_at) FROM cortex_runs GROUP BY repo"
            ).fetchall()
        last_run_by_repo: dict[str, Any] = {r[0]: r[1] for r in rows}
    except Exception:
        last_run_by_repo = {}

    for entry in entries:
        ts = last_run_by_repo.get(entry["repo"])
        entry["last_run"] = str(ts) if ts is not None else None

    return entries


def workspace_status(profiles_dir: Path) -> list[dict[str, Any]]:
    """Return disk and branch stats for each registered workspace.

    Each dict has keys: ``workspace``, ``disk_usage``, ``branch_count``,
    ``oldest_branch``, ``newest_branch``.

    Missing workspace paths yield ``(missing)`` for all filesystem fields.
    No DB queries; no GitHub API calls.
    """
    if not profiles_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for profile_file in sorted(profiles_dir.glob("*/profile.yaml")):
        workspace_name = profile_file.parent.name
        workspace_path = profile_file.parent / "repo"

        if not workspace_path.exists():
            entries.append(
                {
                    "workspace": workspace_name,
                    "disk_usage": "(missing)",
                    "branch_count": "(missing)",
                    "oldest_branch": "(missing)",
                    "newest_branch": "(missing)",
                }
            )
            continue

        # --- disk usage ---
        du = subprocess.run(
            ["du", "-sh", str(workspace_path)],
            capture_output=True,
            text=True,
        )
        disk_usage = du.stdout.split("\t")[0].strip() if du.returncode == 0 else "?"

        # --- branch info via for-each-ref ---
        ref = subprocess.run(
            [
                "git",
                "-C",
                str(workspace_path),
                "for-each-ref",
                "--sort=creatordate",
                "--format=%(creatordate:short)",
                "refs/heads/",
            ],
            capture_output=True,
            text=True,
        )
        dates = [ln.strip() for ln in ref.stdout.splitlines() if ln.strip()]
        branch_count = len(dates)
        oldest_branch = dates[0] if dates else "\u2014"
        newest_branch = dates[-1] if dates else "\u2014"

        entries.append(
            {
                "workspace": workspace_name,
                "disk_usage": disk_usage,
                "branch_count": branch_count,
                "oldest_branch": oldest_branch,
                "newest_branch": newest_branch,
            }
        )

    return entries


def _default_branch(workspace_path: Path) -> str:
    """Return the repo's default branch by reading origin/HEAD; fallback to ``main``."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("/")[-1]
    except Exception:
        pass
    return "main"


def reset_workspace(workspace_path: Path, discard: bool) -> None:
    """Hard-reset a workspace clone to its default branch (origin/HEAD).

    Sequence:
      1. Stash (``discard=False``) OR drop (``discard=True``) uncommitted changes.
      2. ``git fetch origin``
      3. ``git checkout <default_branch> && git reset --hard origin/<default_branch>``

    Raises ``FileNotFoundError`` when ``workspace_path`` does not exist so the
    CLI layer can print a user-friendly message and exit 1 without any git calls
    having been made.
    """
    if not workspace_path.exists():
        raise FileNotFoundError(workspace_path)

    cwd = str(workspace_path)

    if discard:
        subprocess.run(["git", "checkout", "--", "."], cwd=cwd, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=cwd, check=True)
    else:
        subprocess.run(["git", "stash", "push", "-m", "cortex-reset"], cwd=cwd, check=True)

    branch = _default_branch(workspace_path)
    subprocess.run(["git", "fetch", "origin"], cwd=cwd, check=True)
    subprocess.run(["git", "checkout", branch], cwd=cwd, check=True)
    subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=cwd, check=True)


_PRUNE_PROTECTED = frozenset({"main", "master"})


def prune_branches(
    workspace_path: Path,
    older_than_days: int,
    remote: bool,
) -> list[str]:
    """Delete merged and stale local branches in ``workspace_path``.

    A branch is prunable if it satisfies either condition:

    (a) Merged into ``main`` — detected via ``git branch --merged main``.
    (b) Last commit older than ``older_than_days`` — detected via
        ``git log -1 --format=%ct refs/heads/<branch>``.

    Protected from deletion: ``main``, ``master``, and the currently
    checked-out branch.

    When ``remote=True``, also runs ``git push origin --delete <branch>``
    for each deleted local branch.

    Returns the list of deleted branch names.
    """
    cwd = str(workspace_path)

    # Current HEAD — never delete the checked-out branch.
    cp = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    current_head = cp.stdout.strip()

    # All local branches.
    cp = subprocess.run(
        ["git", "for-each-ref", "refs/heads/", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    all_branches = [b for b in cp.stdout.splitlines() if b]

    # Branches already merged into main.
    cp = subprocess.run(
        ["git", "branch", "--merged", "main"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    merged: set[str] = {
        line.strip().lstrip("* ") for line in cp.stdout.splitlines() if line.strip()
    }

    protected = _PRUNE_PROTECTED | {current_head}
    cutoff = time.time() - (older_than_days * 86400)

    deleted: list[str] = []
    for branch in all_branches:
        if branch in protected:
            continue

        is_merged = branch in merged

        is_old = False
        if not is_merged:
            cp = subprocess.run(
                ["git", "log", "-1", "--format=%ct", f"refs/heads/{branch}"],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
            )
            ts_str = cp.stdout.strip()
            if ts_str:
                with contextlib.suppress(ValueError):
                    is_old = int(ts_str) < cutoff

        if not (is_merged or is_old):
            continue

        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        if remote:
            subprocess.run(
                ["git", "push", "origin", "--delete", branch],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd,
            )
        deleted.append(branch)

    return deleted
