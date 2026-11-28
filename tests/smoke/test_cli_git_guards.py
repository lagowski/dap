"""Declarative guards on the CLI code runtimes (#923, part of #810).

Three opt-in checks run after a successful CLI run and BEFORE any push, so a guard
failure never reaches the remote:

- ``require_nonempty_diff``: the run must leave at least one new commit on the
  branch. Without ``push``, uncommitted changes also count; with ``push`` they don't,
  because they would not be pushed. This is the "silent zero output" run that looks
  green.
- ``append_only``: every commit on the branch before the run must still be in its
  history after it (no amend, rebase or reset over existing work). Rewriting the
  run's OWN new commits is fine.
- ``ancestry_guard``: the remote branch, freshly fetched, must be an ancestor of what
  is about to be pushed, so commits someone else pushed are never dropped.

All reads are pinned to ``refs/heads/<branch>``, never ``HEAD``: a CLI that ends its
turn on another branch must not make us judge or push the wrong commits (cortex #815).
Same real-git fixtures as test_cli_git_workspace.py; nothing is mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.smoke._git_repo import _cli, _commit_body, _git, _remote_sha, _run

pytestmark = pytest.mark.usefixtures("openai_key")

FOREIGN = "foreign.txt"


async def _seed_branch(g: dict[str, Path], branch: str) -> str:
    """A first, ordinary run that leaves one pushed commit on ``branch``."""
    first = await _run(
        g["work"], _cli(g["tmp"], _commit_body("seed.txt"), "seed-cli"), branch=branch, push=True
    )
    assert first.success, first.errors
    return _git(g["work"], "rev-parse", f"refs/heads/{branch}")


def _foreign_push(g: dict[str, Path], branch: str) -> str:
    """Shell that makes someone else push a commit to ``branch`` (run by the fake CLI)."""
    seed = g["seed"]
    return (
        f"git -C {seed} fetch -q origin\n"
        f"git -C {seed} checkout -q -B {branch} origin/{branch}\n"
        f"echo x > {seed}/{FOREIGN}\n"
        f"git -C {seed} add {FOREIGN}\n"
        f"git -C {seed} commit -q -m foreign\n"
        f"git -C {seed} push -q origin {branch}\n"
    )


def _git_info(result: Any) -> dict[str, Any]:
    assert result.structured is not None
    info: dict[str, Any] = result.structured["git"]
    return info


# --------------------------------------------------------------------------- defaults


async def test_guards_are_off_by_default(git_repo: dict[str, Path]) -> None:
    # A run that changes nothing still succeeds and pushes, exactly as before #923.
    result = await _run(git_repo["work"], _cli(git_repo["tmp"], ""), branch="feat/noop", push=True)

    assert result.success, result.errors
    assert _remote_sha(git_repo["remote"], "feat/noop") == _remote_sha(
        git_repo["remote"], "develop"
    )


# --------------------------------------------------------------------------- require_nonempty_diff


async def test_nonempty_diff_fails_a_run_that_changed_nothing_and_pushes_nothing(
    git_repo: dict[str, Path],
) -> None:
    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], ""),
        branch="feat/empty",
        push=True,
        require_nonempty_diff=True,
    )

    assert not result.success
    assert any("require_nonempty_diff" in e for e in result.errors), result.errors
    assert _remote_sha(git_repo["remote"], "feat/empty") is None
    assert _git_info(result)["guard"] == "require_nonempty_diff"
    assert _git_info(result)["pushed"] is False


async def test_nonempty_diff_passes_a_run_that_committed(git_repo: dict[str, Path]) -> None:
    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], _commit_body()),
        branch="feat/work",
        push=True,
        require_nonempty_diff=True,
    )

    assert result.success, result.errors
    assert _remote_sha(git_repo["remote"], "feat/work") == _git(
        git_repo["work"], "rev-parse", "feat/work"
    )


async def test_nonempty_diff_is_scoped_to_this_run_not_to_the_base(
    git_repo: dict[str, Path],
) -> None:
    # The branch is already ahead of develop from an earlier run; this run adds nothing.
    await _seed_branch(git_repo, "feat/again")

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], "", "noop-cli"),
        branch="feat/again",
        require_nonempty_diff=True,
    )

    assert not result.success
    assert any("require_nonempty_diff" in e for e in result.errors), result.errors


async def test_nonempty_diff_with_push_does_not_count_uncommitted_changes(
    git_repo: dict[str, Path],
) -> None:
    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], "echo wip > wip.txt\n"),
        branch="feat/wip",
        push=True,
        require_nonempty_diff=True,
    )

    assert not result.success
    assert any("uncommitted" in e for e in result.errors), result.errors
    assert _remote_sha(git_repo["remote"], "feat/wip") is None


async def test_nonempty_diff_without_push_counts_uncommitted_changes(
    git_repo: dict[str, Path],
) -> None:
    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], "echo wip > wip.txt\n"),
        branch="feat/wip2",
        require_nonempty_diff=True,
    )

    assert result.success, result.errors


# --------------------------------------------------------------------------- append_only


async def test_append_only_refuses_an_amend_of_existing_work(git_repo: dict[str, Path]) -> None:
    seeded = await _seed_branch(git_repo, "feat/amend")
    amend = "echo rewritten > seed.txt\ngit commit -q -a --amend -m rewritten\n"

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], amend, "amend-cli"),
        branch="feat/amend",
        push=True,
        force_with_lease=True,
        append_only=True,
    )

    assert not result.success
    assert any("append_only" in e and seeded[:12] in e for e in result.errors), result.errors
    assert _remote_sha(git_repo["remote"], "feat/amend") == seeded  # nothing pushed
    assert _git_info(result)["guard"] == "append_only"


async def test_append_only_refuses_a_reset_over_existing_work(git_repo: dict[str, Path]) -> None:
    seeded = await _seed_branch(git_repo, "feat/reset")

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], "git reset -q --hard HEAD~1\n", "reset-cli"),
        branch="feat/reset",
        append_only=True,
    )

    assert not result.success
    assert any("append_only" in e and seeded[:12] in e for e in result.errors), result.errors


async def test_append_only_allows_rewriting_the_runs_own_new_commits(
    git_repo: dict[str, Path],
) -> None:
    await _seed_branch(git_repo, "feat/own")
    own = _commit_body("own.txt") + "echo again > own.txt\ngit commit -q -a --amend -m own2\n"

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], own, "own-cli"),
        branch="feat/own",
        push=True,
        append_only=True,
    )

    assert result.success, result.errors


# --------------------------------------------------------------------------- ancestry_guard


async def test_ancestry_guard_refuses_to_drop_commits_someone_else_pushed(
    git_repo: dict[str, Path],
) -> None:
    """The case the lease can't catch: the foreign commit was there AT checkout.

    force_with_lease only checks that origin hasn't moved since checkout, and it hasn't,
    so a CLI that rewrites the branch would wipe the foreign commit. append_only is off
    here, to show that ancestry_guard catches it on its own.
    """
    g = git_repo
    await _seed_branch(g, "feat/shared")
    _git(g["seed"], "fetch", "-q", "origin")
    _git(g["seed"], "checkout", "-q", "-B", "feat/shared", "origin/feat/shared")
    (g["seed"] / FOREIGN).write_text("x\n")
    _git(g["seed"], "add", FOREIGN)
    _git(g["seed"], "commit", "-q", "-m", "foreign")
    _git(g["seed"], "push", "-q", "origin", "feat/shared")
    foreign_sha = _git(g["seed"], "rev-parse", "HEAD")
    rewrite = "git reset -q --hard origin/develop\n" + _commit_body("mine.txt")

    result = await _run(
        g["work"],
        _cli(g["tmp"], rewrite, "rewrite-cli"),
        branch="feat/shared",
        push=True,
        force_with_lease=True,
        ancestry_guard=True,
    )

    assert not result.success
    assert any("ancestry_guard" in e and foreign_sha[:12] in e for e in result.errors), (
        result.errors
    )
    assert _remote_sha(g["remote"], "feat/shared") == foreign_sha


async def test_ancestry_guard_names_itself_when_the_remote_moved_during_the_run(
    git_repo: dict[str, Path],
) -> None:
    g = git_repo
    await _seed_branch(g, "feat/race")

    result = await _run(
        g["work"],
        _cli(g["tmp"], _foreign_push(g, "feat/race") + _commit_body("mine.txt"), "race-cli"),
        branch="feat/race",
        push=True,
        ancestry_guard=True,
    )

    assert not result.success
    assert any("ancestry_guard" in e for e in result.errors), result.errors
    assert _remote_sha(g["remote"], "feat/race") == _git(g["seed"], "rev-parse", "HEAD")


@pytest.mark.parametrize("existing", [False, True])
async def test_ancestry_guard_passes_a_new_branch_and_a_fast_forward(
    git_repo: dict[str, Path], existing: bool
) -> None:
    if existing:
        await _seed_branch(git_repo, "feat/ff")

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], _commit_body("next.txt"), "ff-cli"),
        branch="feat/ff",
        push=True,
        ancestry_guard=True,
    )

    assert result.success, result.errors
    assert _remote_sha(git_repo["remote"], "feat/ff") == _git(
        git_repo["work"], "rev-parse", "feat/ff"
    )


# --------------------------------------------------------------------------- pinned to the branch


async def test_push_sends_the_branch_even_if_the_cli_ends_on_another_branch(
    git_repo: dict[str, Path],
) -> None:
    g = git_repo
    wander = _commit_body() + "git checkout -q develop\n"

    result = await _run(g["work"], _cli(g["tmp"], wander), branch="feat/wander", push=True)

    assert result.success, result.errors
    branch_tip = _git(g["work"], "rev-parse", "refs/heads/feat/wander")
    assert _remote_sha(g["remote"], "feat/wander") == branch_tip
    assert branch_tip != _remote_sha(g["remote"], "develop")
    assert _git_info(result)["head_sha"] == branch_tip


async def test_nonempty_diff_judges_the_branch_not_wherever_head_ended_up(
    git_repo: dict[str, Path],
) -> None:
    # The CLI commits on develop instead of the branch: the branch gained nothing.
    stray = "git checkout -q develop\n" + _commit_body("stray.txt")

    result = await _run(
        git_repo["work"],
        _cli(git_repo["tmp"], stray),
        branch="feat/stray",
        push=True,
        require_nonempty_diff=True,
    )

    assert not result.success
    assert any("require_nonempty_diff" in e for e in result.errors), result.errors
    assert _remote_sha(git_repo["remote"], "feat/stray") is None


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("git", "fragment"),
    [
        ({"require_nonempty_diff": True}, "require_nonempty_diff"),
        ({"append_only": True}, "append_only"),
        ({"branch": "feat/x", "ancestry_guard": True}, "ancestry_guard"),
        ({"branch": "feat/x", "append_only": "yes"}, "append_only"),
    ],
)
async def test_invalid_guard_config_is_rejected_before_anything_runs(
    git_repo: dict[str, Path], git: dict[str, Any], fragment: str
) -> None:
    marker = git_repo["tmp"] / "ran"

    result = await _run(git_repo["work"], _cli(git_repo["tmp"], f"touch {marker}\n"), **git)

    assert not result.success
    assert any(fragment in e for e in result.errors), result.errors
    assert not marker.exists()
