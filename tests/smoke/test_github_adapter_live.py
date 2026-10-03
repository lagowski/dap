"""The `github` runtime against the real GitHub API (#920). Opt-in.

Skipped unless both are set:

- ``DAP_GITHUB_IT_TOKEN``: a token that can read the repo
- ``DAP_GITHUB_IT_REPO``: ``owner/name``

Optional: ``DAP_GITHUB_IT_ISSUE`` (default 1) and ``DAP_GITHUB_IT_PR`` (the PR test is
skipped without it). Read-only; nothing is written to the repo.
"""

from __future__ import annotations

import os

import pytest
from dap_runtimes.adapters.github import GithubAdapter
from dap_types import RuntimeTask

TOKEN = os.environ.get("DAP_GITHUB_IT_TOKEN", "")
REPO = os.environ.get("DAP_GITHUB_IT_REPO", "")

pytestmark = pytest.mark.skipif(
    not (TOKEN and REPO), reason="set DAP_GITHUB_IT_TOKEN and DAP_GITHUB_IT_REPO to run"
)


def _task(config: dict[str, object]) -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-920-live",
        prompt_xml="<prompt/>",
        working_directory=".",
        runtime_config={**config, "repo": REPO, "__pipeline_state": {}},
        project_env_vars={"GH_TOKEN": TOKEN},
    )


async def test_reads_a_real_issue() -> None:
    number = int(os.environ.get("DAP_GITHUB_IT_ISSUE", "1"))

    result = await GithubAdapter().execute(_task({"op": "read_issue", "issue": number}))

    assert result.success, result.errors
    assert result.structured is not None
    issue = result.structured["state_delta"]["github_issue"]
    assert issue["number"] == number
    assert issue["url"].endswith(f"/{number}")
    assert TOKEN not in result.model_dump_json()


@pytest.mark.skipif(not os.environ.get("DAP_GITHUB_IT_PR"), reason="set DAP_GITHUB_IT_PR")
async def test_reads_a_real_pr_with_its_files() -> None:
    number = int(os.environ["DAP_GITHUB_IT_PR"])

    result = await GithubAdapter().execute(_task({"op": "read_pr", "pr": number}))

    assert result.success, result.errors
    assert result.structured is not None
    pr = result.structured["state_delta"]["github_pr"]
    assert pr["number"] == number
    assert pr["head"]["sha"]
    assert isinstance(pr["files"], list)
    assert pr["files"]
