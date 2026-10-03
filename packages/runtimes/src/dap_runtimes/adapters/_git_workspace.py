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

GIT_CONFIG_KEYS: Final = ("workspace", "branch", "base", "push", "force_with_lease", "token_env")
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

    def info(self, *, head_sha: str | None, pushed: bool) -> dict[str, Any]:
        return {
            "workspace": self.path,
            "branch": self.config.branch,
            "base": self.config.base,
            "start_sha": self.start_sha,
            "head_sha": head_sha,
            "pushed": pushed,
        }

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

    def _str(key: str) -> tuple[str | None, str | None]:
        value = present.get(key)
        if value is None:
            return (None, None)
        if not isinstance(value, str):
            return (None, f"runtime_config.{key} must be a non-empty string")
        return (value.strip(), None)

    def _bool(key: str) -> tuple[bool, str | None]:
        value = present.get(key, False)
        if not isinstance(value, bool):
            return (False, f"runtime_config.{key} must be true or false")
        return (value, None)

    workspace, error = _str("workspace")
    if error is None:
        branch, error = _str("branch")
    if error is None:
        base, error = _str("base")
    if error is None:
        token_env, error = _str("token_env")
    if error is None:
        push, error = _bool("push")
    if error is None:
        force_with_lease, error = _bool("force_with_lease")
    if error is not None:
        return (None, error)

    if push and branch is None:
        return (None, "runtime_config.push requires runtime_config.branch")
    if force_with_lease and not push:
        return (None, "runtime_config.force_with_lease requires runtime_config.push")

    return (
        GitWorkspaceConfig(
            workspace=workspace,
            branch=branch,
            base=base or DEFAULT_BASE,
            push=push,
            force_with_lease=force_with_lease,
            token_env=token_env,
        ),
        None,
    )


def _is_unset(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, str) and not value.strip())


def _auth_env(env: dict[str, str], token: str) -> dict[str, str]:
    """Add a github.com auth header through ``GIT_CONFIG_*``, keeping it out of argv."""
    out = dict(env)
    index = int(out.get("GIT_CONFIG_COUNT", "0") or 0)
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
    return await _sha(ws, "HEAD")


async def push_branch(ws: GitWorkspace) -> str | None:
    """Push HEAD to ``origin/<branch>``. Returns an error message, or ``None``."""
    branch = ws.config.branch
    assert branch is not None  # parse_git_config guarantees it when push is set
    args = ["push", "--quiet", REMOTE, f"HEAD:refs/heads/{branch}"]
    if ws.config.force_with_lease:
        # Expect the tip seen at checkout; an empty value means "must not exist yet".
        args.insert(1, f"--force-with-lease=refs/heads/{branch}:{ws.lease_sha or ''}")
    code, _, stderr = await _git(ws, *args)
    if code != 0:
        return f"git push to {REMOTE}/{branch} failed: {_tail(ws, stderr)}"
    logger.info("git workspace %s: pushed %s", ws.path, branch)
    return None
