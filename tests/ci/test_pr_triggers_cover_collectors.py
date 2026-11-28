"""PR-validation workflows also run on item PRs into collector branches.

A VeraCrew feature walk opens each item PR into a collector (`feature/<slug>` or
`sprint/<slug>`). With `pull_request.branches: [develop, main]` those PRs got no checks
at all, and the walk, which waits for checks before it merges an item, stopped after its
first item (#941). Only the `pull_request` filter widens: pushes to a collector must not
publish images or run the develop-only jobs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
PR_VALIDATION = ["ci.yml", "e2e.yml", "codeql.yml", "docker-build.yml"]
COLLECTORS = {"feature/**", "sprint/**"}


def _trigger_branches(workflow: str, event: str) -> set[str] | None:
    """The `branches:` list of one top-level `on:` event, or None if it has none."""
    text = (WORKFLOWS / workflow).read_text()
    block = re.search(rf"^  {event}:\n((?:    .*\n)*)", text, re.MULTILINE)
    assert block, f"{workflow} has no `{event}:` trigger"
    branches = re.search(r"^    branches: \[(.*)\]$", block.group(1), re.MULTILINE)
    if branches is None:
        return None
    return {b.strip().strip("\"'") for b in branches.group(1).split(",")}


@pytest.mark.parametrize("workflow", PR_VALIDATION)
def test_pull_request_trigger_covers_collectors(workflow: str) -> None:
    assert _trigger_branches(workflow, "pull_request") == {"develop", "main"} | COLLECTORS


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        ("ci.yml", {"develop", "main"}),
        ("codeql.yml", {"develop", "main"}),
        ("docker-build.yml", {"develop"}),
    ],
)
def test_push_trigger_excludes_collectors(workflow: str, expected: set[str]) -> None:
    assert _trigger_branches(workflow, "push") == expected
