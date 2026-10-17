"""The shared git workspace config through the aider runtime (#929, on #922/#923).

A real bare remote and clone (the ``git_repo`` fixture) and a real executable standing
in for ``aider``: it reads the prompt from the ``--message-file`` it is given (the
adapter passes ``/dev/stdin``), does the scenario's git work the way aider's
auto-commit would, and prints aider's plain-text output. Nothing is mocked.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
from dap_runtimes import AiderAdapter
from dap_types import RuntimeTask

from tests.smoke._git_repo import _commit_body, _git, _remote_sha

_TOKENS = 'echo "Tokens: 1.2k sent, 80 received. Cost: \\$0.01 message, \\$0.01 session."\n'


@pytest.fixture(autouse=True)
def anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """AiderAdapter checks the model's provider key; the stand-in ignores it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


def _aider(tmp: Path, body: str) -> str:
    """A stand-in ``aider``: reads the --message-file into prompt.txt, runs `body`."""
    script = tmp / "aider"
    script.write_text(
        "#!/bin/sh\nset -e\n"
        'while [ $# -gt 0 ]; do [ "$1" = --message-file ] && msg="$2"; shift; done\n'
        'cat "$msg" > "$TMPDIR_PROMPT"\n' + body + _TOKENS
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


async def _run(git_repo: dict[str, Path], binary: str, **git: Any) -> Any:
    config: dict[str, Any] = {"model_id": "claude-sonnet-4-5", "binary_path": binary, **git}
    task = RuntimeTask(
        execution_id="exec-929",
        prompt_xml="<prompt>add a change</prompt>",
        working_directory=str(git_repo["work"]),
        runtime_config=config,
        project_env_vars={"TMPDIR_PROMPT": str(git_repo["tmp"] / "prompt.txt")},
    )
    return await AiderAdapter().execute(task)


async def test_branch_push_and_nonempty_diff_work_through_aider(
    git_repo: dict[str, Path],
) -> None:
    work, remote = git_repo["work"], git_repo["remote"]
    binary = _aider(git_repo["tmp"], _commit_body())

    result = await _run(
        git_repo, binary, branch="feat/aider", push=True, require_nonempty_diff=True
    )

    assert result.success, result.errors
    assert (git_repo["tmp"] / "prompt.txt").read_text() == "<prompt>add a change</prompt>"
    assert _remote_sha(remote, "feat/aider") == _git(work, "rev-parse", "feat/aider")
    assert _git(work, "log", "-1", "--format=%s", "feat/aider") == "cli: change.txt"
    git = result.structured["git"]
    assert git["branch"] == "feat/aider"
    assert git["pushed"] is True
    assert result.structured["changed_files"] == ["change.txt"]
    assert result.tokens_used == 1280


async def test_nonempty_diff_fails_an_aider_run_that_committed_nothing(
    git_repo: dict[str, Path],
) -> None:
    remote = git_repo["remote"]
    binary = _aider(git_repo["tmp"], "")

    result = await _run(
        git_repo, binary, branch="feat/empty", push=True, require_nonempty_diff=True
    )

    assert result.success is False
    assert any("require_nonempty_diff" in e for e in result.errors)
    assert result.structured["git"]["pushed"] is False
    assert _remote_sha(remote, "feat/empty") is None


_ARTIFACTS = (
    "mkdir -p .aider.tags.cache.v4 && echo x > .aider.tags.cache.v4/cache.db\n"
    "echo history > .aider.chat.history.md\n"
)


async def test_aider_files_do_not_dirty_the_workspace_for_the_next_run(
    git_repo: dict[str, Path],
) -> None:
    """aider's cache is ignored locally, so the next branch checkout isn't refused."""
    work = git_repo["work"]
    binary = _aider(git_repo["tmp"], _ARTIFACTS + _commit_body())

    first = await _run(git_repo, binary, branch="feat/one")
    second = await _run(git_repo, binary, branch="feat/two")

    assert first.success, first.errors
    assert second.success, second.errors
    assert (work / ".aider.tags.cache.v4" / "cache.db").exists()
    assert _git(work, "status", "--porcelain") == ""
    assert ".gitignore" not in _git(work, "ls-files")


async def test_aider_files_alone_do_not_satisfy_nonempty_diff(
    git_repo: dict[str, Path],
) -> None:
    """Without push, uncommitted changes count; aider's own cache must not be one."""
    binary = _aider(git_repo["tmp"], _ARTIFACTS)

    result = await _run(git_repo, binary, branch="feat/cache", require_nonempty_diff=True)

    assert result.success is False
    assert any("require_nonempty_diff" in e for e in result.errors)
