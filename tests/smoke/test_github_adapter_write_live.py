"""The `github` runtime's write ops against a real scratch repo (#921). Opt-in.

Skipped unless all are set:

- ``DAP_GITHUB_IT_TOKEN``: a token that can write to the scratch repo
- ``DAP_GITHUB_IT_WRITE_REPO``: ``owner/name`` of a SCRATCH repo (never a real one)
- ``DAP_GITHUB_IT_WRITE_ISSUE``: an issue number in it that the test may edit

Runs comment → update_issue_section → create_branch → open_pr and cleans up after
itself: the comment is deleted, the issue body restored, the PR closed and the
branches deleted. The PR targets a temporary base branch, never the default one.
``merge_pr`` runs only with ``DAP_GITHUB_IT_ALLOW_MERGE=1``, and it merges into that
temporary base too.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

import httpx
import pytest
from dap_runtimes.adapters.github import GithubAdapter
from dap_types import RuntimeTask

TOKEN = os.environ.get("DAP_GITHUB_IT_TOKEN", "")
REPO = os.environ.get("DAP_GITHUB_IT_WRITE_REPO", "")
ISSUE = int(os.environ.get("DAP_GITHUB_IT_WRITE_ISSUE", "0") or 0)
ALLOW_MERGE = os.environ.get("DAP_GITHUB_IT_ALLOW_MERGE") == "1"
API = f"https://api.github.com/repos/{REPO}"

pytestmark = pytest.mark.skipif(
    not (TOKEN and REPO and ISSUE),
    reason="set DAP_GITHUB_IT_TOKEN, DAP_GITHUB_IT_WRITE_REPO and DAP_GITHUB_IT_WRITE_ISSUE",
)


async def _op(config: dict[str, Any]) -> dict[str, Any]:
    task = RuntimeTask(
        execution_id="exec-921-live",
        prompt_xml="<prompt/>",
        working_directory=".",
        runtime_config={"repo": REPO, **config, "__pipeline_state": {}},
        project_env_vars={"GH_TOKEN": TOKEN},
    )
    result = await GithubAdapter().execute(task)
    assert result.success, result.errors
    assert TOKEN not in result.model_dump_json()
    assert result.structured is not None
    delta: dict[str, Any] = next(iter(result.structured["state_delta"].values()))
    return delta


async def test_write_ops_against_a_scratch_repo() -> None:
    tag = uuid.uuid4().hex[:8]
    base_branch, head_branch = f"dap-it/{tag}-base", f"dap-it/{tag}"
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as gh:
        issue = (await gh.get(f"{API}/issues/{ISSUE}")).json()
        original_body = issue.get("body") or ""
        default = (await gh.get(API)).json()["default_branch"]
        comment_id: int | None = None
        pr_number: int | None = None
        try:
            # comment
            comment = await _op(
                {"op": "comment", "issue": ISSUE, "body": f"dap #921 live test {tag}"}
            )
            comment_id = comment["id"]
            assert comment["url"]

            # update_issue_section (twice: the second must be a no-op)
            marked = f"{original_body}\n\n<!-- dap:section:it -->\nold\n<!-- /dap:section:it -->\n"
            await gh.patch(f"{API}/issues/{ISSUE}", json={"body": marked})
            section = {
                "op": "update_issue_section",
                "issue": ISSUE,
                "section": "it",
                "content": tag,
            }
            assert (await _op(section))["changed"] is True
            assert (await _op(section))["changed"] is False

            # create_branch: a temporary base, then the head on top of it
            await _op({"op": "create_branch", "branch": base_branch, "base": default})
            made = await _op({"op": "create_branch", "branch": head_branch, "base": base_branch})
            assert made["created"] is True

            # one commit on the head, so there's something to open a PR for
            await gh.put(
                f"{API}/contents/dap-it/{tag}.txt",
                json={
                    "message": f"dap #921 live test {tag}",
                    "content": base64.b64encode(tag.encode()).decode(),
                    "branch": head_branch,
                },
            )

            # open_pr (twice: the second returns the same PR)
            pr_config = {
                "op": "open_pr",
                "head": head_branch,
                "base": base_branch,
                "title": f"dap #921 live test {tag}",
                "draft": True,
            }
            opened = await _op(pr_config)
            pr_number = opened["number"]
            assert opened["created"] is True
            again = await _op(pr_config)
            assert again == {**opened, "created": False}

            if ALLOW_MERGE:
                await gh.patch(f"{API}/pulls/{pr_number}", json={"draft": False})
                await gh.post(f"{API}/pulls/{pr_number}/ready_for_review")
                head_sha = (await gh.get(f"{API}/pulls/{pr_number}")).json()["head"]["sha"]
                merged = await _op(
                    {"op": "merge_pr", "pr": pr_number, "expected_head_sha": head_sha}
                )
                assert merged["merged"] is True
        finally:
            if comment_id is not None:
                await gh.delete(f"{API}/issues/comments/{comment_id}")
            await gh.patch(f"{API}/issues/{ISSUE}", json={"body": original_body})
            if pr_number is not None:
                await gh.patch(f"{API}/pulls/{pr_number}", json={"state": "closed"})
            for branch in (head_branch, base_branch):
                await gh.delete(f"{API}/git/refs/heads/{branch}")
