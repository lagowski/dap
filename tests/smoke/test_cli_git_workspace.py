"""Declarative git workspace config on the CLI code runtimes (#922, part of #810).

``workspace`` / ``branch`` / ``base`` / ``push`` / ``force_with_lease`` / ``token_env``
on ``runtime_config`` let a pipeline say "run the coder on branch X off develop, then
push" without a python-func node. They live in the shared ``_BaseCliAdapter``, so every
CLI runtime built on it (claude-code, codex, gemini-cli) gets them.

Everything here runs against a REAL git setup — a bare "remote" and a clone of it — and
a real executable standing in for the CLI: a shell script that reads the prompt, does
whatever git work the scenario needs, and prints the codex JSON shape. Nothing is mocked,
so these tests prove what actually lands on the remote.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from dap_runtimes.adapters._cli_base import _BaseCliAdapter
from dap_runtimes.adapters.codex import CodexAdapter

from tests.smoke._git_repo import (
    TOKEN,
    _cli,
    _commit_body,
    _git,
    _remote_sha,
    _run,
    _task,
)

pytestmark = pytest.mark.usefixtures("openai_key")

# --------------------------------------------------------------------------- unchanged


async def test_without_git_config_behaviour_is_unchanged(git_repo: dict[str, Path]) -> None:
    work = git_repo["work"]
    binary = _cli(git_repo["tmp"], "git rev-parse --abbrev-ref HEAD > where.txt\n")

    result = await _run(work, binary)

    assert result.success, result.errors
    assert "git" not in (result.structured or {})
    assert (work / "where.txt").read_text().strip() == "develop"
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == "develop"


@pytest.mark.parametrize(
    "inert",
    [
        {"push": False, "force_with_lease": False},
        {"branch": "", "workspace": "  ", "base": None, "token_env": ""},
    ],
)
async def test_unset_values_left_by_the_agent_form_change_nothing(
    git_repo: dict[str, Path], inert: dict[str, Any]
) -> None:
    """The dashboard writes `false` for an untoggled checkbox and `""` for a cleared input.

    An agent whose git fields were touched and then cleared must behave exactly like one
    that never had them.
    """
    work = git_repo["work"]
    binary = _cli(git_repo["tmp"], "git rev-parse --abbrev-ref HEAD > where.txt\n")

    result = await _run(work, binary, **inert)

    assert result.success, result.errors
    assert "git" not in (result.structured or {})
    assert (work / "where.txt").read_text().strip() == "develop"


# --------------------------------------------------------------------------- checkout


async def test_branch_is_created_from_base_before_the_cli_runs(git_repo: dict[str, Path]) -> None:
    work, remote = git_repo["work"], git_repo["remote"]
    binary = _cli(git_repo["tmp"], "git rev-parse --abbrev-ref HEAD > where.txt\n")

    result = await _run(work, binary, branch="feat/x", base="develop")

    assert result.success, result.errors
    assert (work / "where.txt").read_text().strip() == "feat/x"
    assert _git(work, "rev-parse", "feat/x") == _remote_sha(remote, "develop")
    git = result.structured["git"]
    assert git["branch"] == "feat/x"
    assert git["base"] == "develop"
    assert git["start_sha"] == _remote_sha(remote, "develop")
    assert git["pushed"] is False


async def test_existing_local_branch_is_checked_out(git_repo: dict[str, Path]) -> None:
    work = git_repo["work"]
    _git(work, "checkout", "-q", "-b", "feat/local")
    (work / "local.txt").write_text("x\n")
    _git(work, "add", "local.txt")
    _git(work, "commit", "-q", "-m", "local work")
    local_tip = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "develop")
    binary = _cli(git_repo["tmp"], "git rev-parse HEAD > head.txt\n")

    result = await _run(work, binary, branch="feat/local")

    assert result.success, result.errors
    assert (work / "head.txt").read_text().strip() == local_tip


async def test_branch_that_exists_only_on_the_remote_starts_from_its_tip(
    git_repo: dict[str, Path],
) -> None:
    work, seed, remote = git_repo["work"], git_repo["seed"], git_repo["remote"]
    _git(seed, "checkout", "-q", "-b", "feat/remote")
    (seed / "remote.txt").write_text("r\n")
    _git(seed, "add", "remote.txt")
    _git(seed, "commit", "-q", "-m", "remote work")
    _git(seed, "push", "-q", "origin", "feat/remote")
    binary = _cli(git_repo["tmp"], "git rev-parse HEAD > head.txt\n")

    result = await _run(work, binary, branch="feat/remote")

    assert result.success, result.errors
    assert (work / "head.txt").read_text().strip() == _remote_sha(remote, "feat/remote")


async def test_relative_workspace_resolves_against_the_working_directory(
    git_repo: dict[str, Path],
) -> None:
    binary = _cli(git_repo["tmp"], "git rev-parse --abbrev-ref HEAD > where.txt\n")

    result = await CodexAdapter().execute(
        _task(git_repo["tmp"], binary, workspace="work", branch="feat/rel")
    )

    assert result.success, result.errors
    assert (git_repo["work"] / "where.txt").read_text().strip() == "feat/rel"
    assert result.structured is not None
    assert result.structured["git"]["workspace"] == str(git_repo["work"])


# --------------------------------------------------------------------------- push


async def test_push_lands_the_cli_commit_on_the_remote_branch(git_repo: dict[str, Path]) -> None:
    work, remote = git_repo["work"], git_repo["remote"]
    binary = _cli(git_repo["tmp"], _commit_body())

    result = await _run(work, binary, branch="feat/push", push=True)

    assert result.success, result.errors
    assert _remote_sha(remote, "feat/push") == _git(work, "rev-parse", "HEAD")
    assert result.structured["git"]["pushed"] is True
    assert result.structured["git"]["head_sha"] == _git(work, "rev-parse", "HEAD")


async def test_failed_cli_run_pushes_nothing(git_repo: dict[str, Path]) -> None:
    remote = git_repo["remote"]
    binary = _cli(git_repo["tmp"], _commit_body() + "exit 3\n")

    result = await _run(git_repo["work"], binary, branch="feat/fail", push=True)

    assert not result.success
    assert _remote_sha(remote, "feat/fail") is None


async def test_plain_push_refuses_when_the_remote_branch_moved(git_repo: dict[str, Path]) -> None:
    work, seed, remote = git_repo["work"], git_repo["seed"], git_repo["remote"]
    _git(seed, "checkout", "-q", "-b", "feat/race")
    _git(seed, "push", "-q", "origin", "feat/race")
    # While the CLI runs, someone else pushes to the same branch.
    foreign = (
        f"git -C {seed} commit -q --allow-empty -m foreign\n"
        f"git -C {seed} push -q origin feat/race\n"
    )
    binary = _cli(git_repo["tmp"], foreign + _commit_body())

    result = await _run(work, binary, branch="feat/race", push=True)

    assert not result.success
    assert any("push" in e.lower() for e in result.errors), result.errors
    assert _remote_sha(remote, "feat/race") == _git(seed, "rev-parse", "HEAD")  # foreign kept


async def test_force_with_lease_overwrites_a_rewritten_branch_nobody_else_touched(
    git_repo: dict[str, Path],
) -> None:
    work, remote = git_repo["work"], git_repo["remote"]
    first = await _run(work, _cli(git_repo["tmp"], _commit_body()), branch="feat/lease", push=True)
    assert first.success, first.errors
    amend = "echo amended > change.txt\ngit commit -q -a --amend -m amended\n"

    result = await _run(
        work,
        _cli(git_repo["tmp"], amend, "amend-cli"),
        branch="feat/lease",
        push=True,
        force_with_lease=True,
    )

    assert result.success, result.errors
    assert _remote_sha(remote, "feat/lease") == _git(work, "rev-parse", "HEAD")


async def test_force_with_lease_refuses_when_the_remote_moved_since_checkout(
    git_repo: dict[str, Path],
) -> None:
    work, seed, remote = git_repo["work"], git_repo["seed"], git_repo["remote"]
    first = await _run(work, _cli(git_repo["tmp"], _commit_body()), branch="feat/lease2", push=True)
    assert first.success, first.errors
    _git(seed, "fetch", "-q", "origin")
    _git(seed, "checkout", "-q", "-b", "feat/lease2", "origin/feat/lease2")
    foreign = (
        f"git -C {seed} commit -q --allow-empty -m foreign\n"
        f"git -C {seed} push -q origin feat/lease2\n"
    )
    amend = "echo amended > change.txt\ngit commit -q -a --amend -m amended\n"

    result = await _run(
        work,
        _cli(git_repo["tmp"], foreign + amend, "race-cli"),
        branch="feat/lease2",
        push=True,
        force_with_lease=True,
    )

    assert not result.success
    assert _remote_sha(remote, "feat/lease2") == _git(seed, "rev-parse", "HEAD")  # foreign kept


# --------------------------------------------------------------------------- refusals


async def test_dirty_workspace_fails_without_running_the_cli(git_repo: dict[str, Path]) -> None:
    work = git_repo["work"]
    (work / "README").write_text("uncommitted\n")
    marker = git_repo["tmp"] / "ran"
    binary = _cli(git_repo["tmp"], f"touch {marker}\n")

    result = await _run(work, binary, branch="feat/dirty")

    assert not result.success
    assert any("uncommitted" in e for e in result.errors), result.errors
    assert not marker.exists()


@pytest.mark.parametrize("workspace", ["missing-dir", "not-a-repo"])
async def test_invalid_workspace_fails_without_running_the_cli(
    git_repo: dict[str, Path], workspace: str
) -> None:
    (git_repo["tmp"] / "not-a-repo").mkdir()
    marker = git_repo["tmp"] / "ran"
    binary = _cli(git_repo["tmp"], f"touch {marker}\n")

    result = await CodexAdapter().execute(
        _task(git_repo["tmp"], binary, workspace=workspace, branch="feat/x")
    )

    assert not result.success
    assert not marker.exists()


async def test_unknown_base_fails_without_running_the_cli(git_repo: dict[str, Path]) -> None:
    marker = git_repo["tmp"] / "ran"
    binary = _cli(git_repo["tmp"], f"touch {marker}\n")

    result = await _run(git_repo["work"], binary, branch="feat/x", base="no-such-base")

    assert not result.success
    assert any("no-such-base" in e for e in result.errors), result.errors
    assert not marker.exists()


@pytest.mark.parametrize(
    ("git", "fragment"),
    [
        ({"push": True}, "branch"),
        ({"branch": "bad..name"}, "branch"),
        ({"branch": "feat/x", "push": "yes"}, "push"),
        ({"branch": "feat/x", "force_with_lease": True}, "force_with_lease"),
        ({"workspace": 5}, "workspace"),
        ({"branch": "feat/x", "base": 7}, "base"),
        ({"branch": "feat/x", "token_env": 5}, "token_env"),
    ],
)
async def test_invalid_git_config_is_rejected_before_anything_runs(
    git_repo: dict[str, Path], git: dict[str, Any], fragment: str
) -> None:
    marker = git_repo["tmp"] / "ran"
    binary = _cli(git_repo["tmp"], f"touch {marker}\n")

    result = await _run(git_repo["work"], binary, **git)

    assert not result.success
    assert any(fragment in e for e in result.errors), result.errors
    assert not marker.exists()
    assert _git(git_repo["work"], "rev-parse", "--abbrev-ref", "HEAD") == "develop"


# --------------------------------------------------------------------------- token


async def test_named_token_env_that_is_unset_fails_before_the_cli(
    git_repo: dict[str, Path],
) -> None:
    marker = git_repo["tmp"] / "ran"
    binary = _cli(git_repo["tmp"], f"touch {marker}\n")

    result = await _run(
        git_repo["work"], binary, branch="feat/x", push=True, token_env="DAP_TEST_NO_SUCH_TOKEN"
    )

    assert not result.success
    assert any("DAP_TEST_NO_SUCH_TOKEN" in e for e in result.errors), result.errors
    assert not marker.exists()


async def test_token_never_appears_in_the_result_or_logs(
    git_repo: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    work, seed = git_repo["work"], git_repo["seed"]
    _git(seed, "checkout", "-q", "-b", "feat/tok")
    _git(seed, "push", "-q", "origin", "feat/tok")
    # Force a push failure so the error path, the likeliest leak, is exercised too.
    foreign = (
        f"git -C {seed} commit -q --allow-empty -m foreign\ngit -C {seed} push -q origin feat/tok\n"
    )
    task = _task(
        work,
        _cli(git_repo["tmp"], foreign + _commit_body()),
        branch="feat/tok",
        push=True,
        token_env="DAP_TEST_GH_TOKEN",
    )
    task = task.model_copy(update={"project_env_vars": {"DAP_TEST_GH_TOKEN": TOKEN}})

    result = await CodexAdapter().execute(task)

    assert not result.success
    assert TOKEN not in result.model_dump_json()
    assert TOKEN not in caplog.text


def test_every_cli_runtime_on_the_shared_base_inherits_the_git_config() -> None:
    from dap_runtimes.adapters.claude_code import ClaudeCodeAdapter
    from dap_runtimes.adapters.gemini_cli import GeminiCliAdapter

    for adapter in (ClaudeCodeAdapter, CodexAdapter, GeminiCliAdapter):
        assert issubclass(adapter, _BaseCliAdapter)
        assert "execute" not in adapter.__dict__, adapter.__name__


@pytest.mark.parametrize(
    ("existing", "index"),
    [({}, 0), ({"GIT_CONFIG_COUNT": "2"}, 2), ({"GIT_CONFIG_COUNT": "junk"}, 0)],
)
def test_auth_header_is_appended_to_git_config_env_without_touching_argv(
    existing: dict[str, str], index: int
) -> None:
    from dap_runtimes.adapters._git_workspace import _auth_env

    env = _auth_env({"PATH": "/bin", **existing}, TOKEN)

    assert env["GIT_CONFIG_COUNT"] == str(index + 1)
    assert env[f"GIT_CONFIG_KEY_{index}"] == "http.https://github.com/.extraheader"
    assert env[f"GIT_CONFIG_VALUE_{index}"].startswith("AUTHORIZATION: basic ")
    assert TOKEN not in env[f"GIT_CONFIG_VALUE_{index}"]  # base64-encoded, not raw
