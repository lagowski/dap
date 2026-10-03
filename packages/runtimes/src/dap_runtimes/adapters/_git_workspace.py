"""Declarative git workspace for the CLI code runtimes (#922, part of #810).

Six optional ``runtime_config`` keys let a pipeline say "run the coder on branch X off
develop, then push" as data instead of a python-func node:

- ``workspace``: directory the CLI runs in; absolute, or relative to the task's
  ``working_directory``. Defaults to ``working_directory`` (the old behaviour).
- ``branch``: checked out before the CLI runs. An existing local branch is used as is;
  one that exists only on ``origin`` starts from its tip; otherwise it is created from
  ``base``.
- ``base``: start point for a new ``branch``; ``origin/<base>`` is preferred over a
  local ``<base>``. Defaults to ``develop``.
- ``push``: after a successful CLI run, push ``branch`` to ``origin``. A plain push is
  fast-forward only.
- ``force_with_lease``: let ``push`` replace a rewritten branch, but only if the remote
  still points where it did when the branch was checked out. Never a plain ``--force``.
- ``token_env``: name of the env var (resolved through DAP's env layering) holding the
  token for git's HTTPS calls to github.com. The token reaches git through
  ``GIT_CONFIG_*`` env vars only, never argv, and is scrubbed from every message.

Three opt-in guards (#923) run after a successful CLI run and before any push, so a guard
failure never reaches the remote:

- ``require_nonempty_diff``: the run must leave at least one new commit on ``branch``.
  Without ``push``, uncommitted changes count too; with it they don't, since they would
  not be pushed (the "silent zero output" run that looks green).
- ``append_only``: every commit on ``branch`` before the run must still be in its history
  afterwards: no amend, rebase or reset over existing work. Rewriting the run's own new
  commits is allowed. (cortex scanned the reflog for such operations, which also flagged
  harmless amends of the run's own commits; ancestry is the precise test.)
- ``ancestry_guard``: the remote branch, freshly fetched, must be an ancestor of what is
  about to be pushed, so commits someone else pushed are never dropped, including ones
  that were already there at checkout, which ``force_with_lease`` can't see.

Everything after the CLI runs reads ``refs/heads/<branch>``, never ``HEAD``: a CLI that ends
its turn on another branch must not make us judge or push the wrong commits (cortex #815).

With none of these keys set, :func:`parse_git_config` returns ``None`` and the adapter
behaves exactly as before.

Git runs in its own module (not ``_cli_base``) so tests that patch the CLI subprocess at
``dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec`` never intercept it.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger("dap.runtimes.git_workspace")

_STRING_KEYS: Final = ("workspace", "branch", "base", "token_env")
_BOOL_KEYS: Final = (
    "push",
    "force_with_lease",
    "require_nonempty_diff",
    "append_only",
    "ancestry_guard",
)
GIT_CONFIG_KEYS: Final = (*_STRING_KEYS, *_BOOL_KEYS)
DEFAULT_BASE: Final = "develop"
REMOTE: Final = "origin"
GIT_TIMEOUT_SECONDS: Final = 120.0
_STDERR_TAIL_LINES: Final = 5


@dataclass(frozen=True)
class GitWorkspaceConfig:
    workspace: str | None
    branch: str | None
    base: str
    push: bool
    force_with_lease: bool
    token_env: str | None
    require_nonempty_diff: bool = False
    append_only: bool = False
    ancestry_guard: bool = False


@dataclass
class GitWorkspace:
    """A prepared workspace: where the CLI runs and what ``push`` may assume."""

    config: GitWorkspaceConfig
    path: str
    env: dict[str, str]
    token: str | None
    start_sha: str | None = None
    # Remote tip of ``branch`` at checkout; ``None`` if it didn't exist there yet.
    lease_sha: str | None = None

    def info(
        self, *, head_sha: str | None, pushed: bool, guard: str | None = None
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "workspace": self.path,
            "branch": self.config.branch,
            "base": self.config.base,
            "start_sha": self.start_sha,
            "head_sha": head_sha,
            "pushed": pushed,
        }
        if guard is not None:
            info["guard"] = guard
        return info

    def redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text


def parse_git_config(
    config: dict[str, Any],
) -> tuple[GitWorkspaceConfig | None, str | None]:
    """Validate the git keys. ``(None, None)`` when none is set.

    ``None``, ``False`` and blank strings count as unset: the agent form writes ``false``
    for a checkbox toggled off and ``""`` for a cleared input, and such an agent must
    behave exactly like one that never had the keys.
    """
    present = {
        key: config[key] for key in GIT_CONFIG_KEYS if key in config and not _is_unset(config[key])
    }
    if not present:
        return (None, None)

    strings: dict[str, str | None] = {}
    for key in _STRING_KEYS:
        value = present.get(key)
        if value is not None and not isinstance(value, str):
            return (None, f"runtime_config.{key} must be a non-empty string")
        strings[key] = value.strip() if isinstance(value, str) else None
    flags: dict[str, bool] = {}
    for key in _BOOL_KEYS:
        value = present.get(key, False)
        if not isinstance(value, bool):
            return (None, f"runtime_config.{key} must be true or false")
        flags[key] = value

    branch = strings["branch"]
    for key in ("push", "require_nonempty_diff", "append_only"):
        if flags[key] and branch is None:
            return (None, f"runtime_config.{key} requires runtime_config.branch")
    for key in ("force_with_lease", "ancestry_guard"):
        if flags[key] and not flags["push"]:
            return (None, f"runtime_config.{key} requires runtime_config.push")

    return (
        GitWorkspaceConfig(
            workspace=strings["workspace"],
            branch=branch,
            base=strings["base"] or DEFAULT_BASE,
            token_env=strings["token_env"],
            **flags,
        ),
        None,
    )


def _is_unset(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, str) and not value.strip())


def _auth_env(env: dict[str, str], token: str) -> dict[str, str]:
    """Add a github.com auth header through ``GIT_CONFIG_*``, keeping it out of argv."""
    out = dict(env)
    count = out.get("GIT_CONFIG_COUNT", "")
    # Append after any entries the engine env already carries; git ignores a malformed
    # count, so treat it as zero instead of raising mid-run.
    index = int(count) if count.isdigit() else 0
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    out[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
    out[f"GIT_CONFIG_VALUE_{index}"] = f"AUTHORIZATION: basic {credentials}"
    out["GIT_CONFIG_COUNT"] = str(index + 1)
    return out


async def _git(ws: GitWorkspace, *args: str) -> tuple[int, str, str]:
    """Run ``git <args>`` in the workspace. Never raises for a git failure."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=ws.path,
            env=ws.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return (-1, "", f"could not run git: {exc}")
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return (-1, "", f"git {args[0]} timed out after {GIT_TIMEOUT_SECONDS:.0f}s")
    return (
        process.returncode if process.returncode is not None else -1,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def _tail(ws: GitWorkspace, stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.strip()][-_STDERR_TAIL_LINES:]
    return ws.redact(" | ".join(lines)) if lines else "no output"


async def _sha(ws: GitWorkspace, ref: str) -> str | None:
    code, out, _ = await _git(ws, "rev-parse", "--verify", "-q", ref)
    return out if code == 0 and out else None


async def prepare_workspace(
    config: GitWorkspaceConfig,
    *,
    working_directory: str,
    env: dict[str, str],
) -> tuple[GitWorkspace | None, str | None]:
    """Resolve the workspace and check out ``branch``. Runs before the CLI.

    Every refusal returns ``(None, error)`` with the workspace left as it was, so the
    CLI is never invoked on the wrong branch or over someone's uncommitted work.
    """
    ws, error = _resolve(config, working_directory=working_directory, env=env)
    if ws is None or config.branch is None:
        return (ws, error)
    error = await _check_clean_repo(ws, config.branch)
    if error is None:
        error = await _checkout(ws, config.branch)
    if error is not None:
        return (None, error)
    ws.start_sha = await _sha(ws, "HEAD")
    logger.info("git workspace %s: on %s at %s", ws.path, config.branch, ws.start_sha)
    return (ws, None)


def _resolve(
    config: GitWorkspaceConfig, *, working_directory: str, env: dict[str, str]
) -> tuple[GitWorkspace | None, str | None]:
    path = working_directory
    if config.workspace is not None:
        path = os.path.normpath(os.path.join(working_directory, config.workspace))
    if not os.path.isdir(path):
        return (None, f"runtime_config.workspace {path} does not exist or is not a directory")
    token: str | None = None
    git_env = {**env, "GIT_TERMINAL_PROMPT": "0"}
    if config.token_env is not None:
        token = env.get(config.token_env) or None
        if token is None:
            return (None, f"runtime_config.token_env names {config.token_env}, which is not set")
        git_env = _auth_env(git_env, token)
    return (GitWorkspace(config=config, path=path, env=git_env, token=token), None)


async def _check_clean_repo(ws: GitWorkspace, branch: str) -> str | None:
    code, _, _ = await _git(ws, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return f"runtime_config.workspace {ws.path} is not a git repository"
    code, _, _ = await _git(ws, "check-ref-format", "--branch", branch)
    if code != 0:
        return f"runtime_config.branch {branch!r} is not a valid branch name"
    code, status, stderr = await _git(ws, "status", "--porcelain")
    if code != 0:
        return f"git status failed in {ws.path}: {_tail(ws, stderr)}"
    if status:
        return f"workspace {ws.path} has uncommitted changes; refusing to switch to {branch}"
    return None


async def _checkout(ws: GitWorkspace, branch: str) -> str | None:
    code, _, _ = await _git(ws, "remote", "get-url", REMOTE)
    has_remote = code == 0
    if has_remote:
        code, _, stderr = await _git(ws, "fetch", "--quiet", "--prune", REMOTE)
        if code != 0:
            return f"git fetch {REMOTE} failed: {_tail(ws, stderr)}"
        ws.lease_sha = await _sha(ws, f"refs/remotes/{REMOTE}/{branch}")

    if await _sha(ws, f"refs/heads/{branch}") is not None:
        checkout = ["checkout", "-q", branch]
    elif ws.lease_sha is not None:
        checkout = ["checkout", "-q", "-b", branch, f"refs/remotes/{REMOTE}/{branch}"]
    else:
        base = ws.config.base
        start = await _sha(ws, f"refs/remotes/{REMOTE}/{base}") if has_remote else None
        start = start or await _sha(ws, f"refs/heads/{base}")
        if start is None:
            return f"runtime_config.base {base!r} not found (looked for {REMOTE}/{base} and {base})"
        checkout = ["checkout", "-q", "-b", branch, start]

    code, _, stderr = await _git(ws, *checkout)
    if code != 0:
        return f"git checkout {branch} failed: {_tail(ws, stderr)}"
    return None


async def head_sha(ws: GitWorkspace) -> str | None:
    """The branch's tip after the run (``HEAD`` only when no branch is configured)."""
    branch = ws.config.branch
    return await _sha(ws, f"refs/heads/{branch}" if branch else "HEAD")


async def run_guards(ws: GitWorkspace) -> tuple[str | None, str | None]:
    """Run the enabled guards. Returns ``(guard_name, error)`` for the first failure."""
    branch = ws.config.branch
    if branch is None:
        return (None, None)
    tip = await _sha(ws, f"refs/heads/{branch}")
    if tip is None:
        return ("branch", f"branch {branch} no longer exists after the run")
    checks = (
        ("append_only", ws.config.append_only, _append_only),
        ("require_nonempty_diff", ws.config.require_nonempty_diff, _nonempty_diff),
        ("ancestry_guard", ws.config.ancestry_guard, _ancestry),
    )
    for name, enabled, check in checks:
        if enabled:
            error = await check(ws, branch, tip)
            if error is not None:
                return (name, f"{name}: {error}")
    return (None, None)


async def _is_ancestor(ws: GitWorkspace, ancestor: str, tip: str) -> bool:
    code, _, _ = await _git(ws, "merge-base", "--is-ancestor", ancestor, tip)
    return code == 0


async def _missing(ws: GitWorkspace, tip: str, other: str) -> str:
    """Short shas reachable from ``other`` but not from ``tip`` (up to five)."""
    _, out, _ = await _git(ws, "rev-list", "--max-count=5", f"{tip}..{other}")
    return ", ".join(sha[:12] for sha in out.split()) or other[:12]


async def _append_only(ws: GitWorkspace, branch: str, tip: str) -> str | None:
    if ws.start_sha is None or await _is_ancestor(ws, ws.start_sha, tip):
        return None
    dropped = await _missing(ws, tip, ws.start_sha)
    return f"the run rewrote existing history on {branch}; no longer on it: {dropped}"


async def _nonempty_diff(ws: GitWorkspace, branch: str, tip: str) -> str | None:
    if ws.start_sha is not None and tip != ws.start_sha:
        return None
    _, status, _ = await _git(ws, "status", "--porcelain")
    if status and not ws.config.push:
        return None
    if status:
        return (
            f"the run left uncommitted changes but no new commit on {branch}; "
            "uncommitted changes are not pushed"
        )
    return f"the run made no change on {branch}"


async def _ancestry(ws: GitWorkspace, branch: str, tip: str) -> str | None:
    code, _, stderr = await _git(ws, "fetch", "--quiet", REMOTE)
    if code != 0:
        return f"could not fetch {REMOTE} to check: {_tail(ws, stderr)}"
    remote_tip = await _sha(ws, f"refs/remotes/{REMOTE}/{branch}")
    if remote_tip is None or await _is_ancestor(ws, remote_tip, tip):
        return None
    foreign = await _missing(ws, tip, remote_tip)
    return f"pushing would drop commits on {REMOTE}/{branch} that this run doesn't have: {foreign}"


async def push_branch(ws: GitWorkspace) -> str | None:
    """Push HEAD to ``origin/<branch>``. Returns an error message, or ``None``."""
    branch = ws.config.branch
    assert branch is not None  # parse_git_config guarantees it when push is set
    # Push the branch itself, not HEAD: the CLI may have left HEAD elsewhere.
    args = ["push", "--quiet", REMOTE, f"refs/heads/{branch}:refs/heads/{branch}"]
    if ws.config.force_with_lease:
        # Expect the tip seen at checkout; an empty value means "must not exist yet".
        args.insert(1, f"--force-with-lease=refs/heads/{branch}:{ws.lease_sha or ''}")
    code, _, stderr = await _git(ws, *args)
    if code != 0:
        return f"git push to {REMOTE}/{branch} failed: {_tail(ws, stderr)}"
    logger.info("git workspace %s: pushed %s", ws.path, branch)
    return None
