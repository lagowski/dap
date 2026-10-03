"""Real-git fixtures for the CLI runtime git tests (#922, #923).

A bare "remote" with `develop`, a clone of it, and an executable that stands in for
the CLI: it drains stdin, runs a scenario's shell body inside the workspace, and
prints the codex JSON shape. Nothing is mocked; tests assert on what actually lands
in the repos.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

from dap_runtimes.adapters.codex import CodexAdapter
from dap_types import RuntimeTask

TOKEN = "ghp_SECRETtoken0123456789abcdefSECRET"
CLI_OK = 'printf \'{"output_text": "done", "usage": {}}\'\n'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_git_repo(tmp_path: Path) -> dict[str, Path]:
    """A bare remote with `develop`, and a clean clone of it."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "develop", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(seed)], check=True)
    for path in (seed,):
        _git(path, "config", "user.name", "Seed")
        _git(path, "config", "user.email", "seed@example.com")
    (seed / "README").write_text("seed\n")
    _git(seed, "add", "README")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-q", "origin", "develop")
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    _git(work, "config", "user.name", "Coder")
    _git(work, "config", "user.email", "coder@example.com")
    return {"remote": remote, "seed": seed, "work": work, "tmp": tmp_path}


def _cli(tmp: Path, body: str, name: str = "fake-cli") -> str:
    """An executable stand-in for the CLI: drains stdin, runs `body`, prints JSON."""
    script = tmp / name
    script.write_text("#!/bin/sh\nset -e\ncat >/dev/null\n" + body + CLI_OK)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _commit_body(name: str = "change.txt") -> str:
    return (
        f'echo "$(git rev-parse --abbrev-ref HEAD)" > {name}\n'
        f"git add {name}\n"
        f'git commit -q -m "cli: {name}"\n'
    )


def _task(work: Path, binary: str, **git: Any) -> RuntimeTask:
    config: dict[str, Any] = {"model_id": "gpt-5-codex", "binary_path": binary, **git}
    return RuntimeTask(
        execution_id="exec-922",
        prompt_xml="<prompt/>",
        working_directory=str(work),
        runtime_config=config,
    )


async def _run(work: Path, binary: str, **git: Any) -> Any:
    return await CodexAdapter().execute(_task(work, binary, **git))


def _remote_sha(remote: Path, branch: str) -> str | None:
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
        cwd=remote,
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or None
