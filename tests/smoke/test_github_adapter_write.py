"""The `github` runtime, write operations (#921, part of #810).

`comment`, `update_issue_section`, `create_branch`, `open_pr` and `merge_pr`. Write
flows depend on GitHub's state (re-runs must be idempotent, a branch or PR may already
exist, a merge must fail when the head moved), so these tests drive a small stateful
fake of the REST API behind ``httpx.MockTransport``: it keeps issues, comments, refs
and pulls in memory and answers with GitHub's real status codes and messages. The
adapter's real client code runs end to end.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from dap_runtimes.adapters.github import GithubAdapter
from dap_types import RuntimeTask

TOKEN = "ghp_SECRETtoken0123456789abcdefSECRET"
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
SECTION_BODY = "Intro\n\n<!-- dap:section:plan -->\nold plan\n<!-- /dap:section:plan -->\n\nOutro\n"


class FakeGitHub:
    """Just enough of the GitHub REST API, with state, for the write ops."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {7: {"number": 7, "body": SECTION_BODY}}
        self.comments: list[dict[str, Any]] = []
        self.refs: dict[str, str] = {"develop": BASE_SHA}
        self.pulls: dict[int, dict[str, Any]] = {
            12: {
                "number": 12,
                "head": {"ref": "feat/x", "sha": HEAD_SHA},
                "merged": False,
                "mergeable": True,
            },
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = request.url.path.removeprefix("/repos/acme/widgets")
        body = json.loads(request.content) if request.content else {}
        for pattern, method, handler in self._routes():
            match = re.fullmatch(pattern, path)
            if match and request.method == method:
                return handler(body, request, *match.groups())
        return httpx.Response(404, json={"message": "Not Found"})

    def _routes(self) -> list[tuple[str, str, Callable[..., httpx.Response]]]:
        return [
            (r"/issues/(\d+)/comments", "POST", self._comment),
            (r"/issues/(\d+)", "GET", self._get_issue),
            (r"/issues/(\d+)", "PATCH", self._patch_issue),
            (r"/git/ref/heads/(.+)", "GET", self._get_ref),
            (r"/git/refs", "POST", self._create_ref),
            (r"/pulls", "POST", self._open_pr),
            (r"/pulls", "GET", self._list_prs),
            (r"/pulls/(\d+)/merge", "PUT", self._merge),
        ]

    def _comment(self, body: dict[str, Any], _r: httpx.Request, n: str) -> httpx.Response:
        if int(n) not in self.issues and int(n) not in self.pulls:
            return httpx.Response(404, json={"message": "Not Found"})
        comment_id = 900 + len(self.comments)
        comment = {
            "id": comment_id,
            "body": body["body"],
            "html_url": f"https://github.com/acme/widgets/issues/{n}#issuecomment-{comment_id}",
        }
        self.comments.append(comment)
        return httpx.Response(201, json=comment)

    def _get_issue(self, _b: dict[str, Any], _r: httpx.Request, n: str) -> httpx.Response:
        issue = self.issues.get(int(n))
        if issue is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200, json={**issue, "html_url": f"https://github.com/acme/widgets/issues/{n}"}
        )

    def _patch_issue(self, body: dict[str, Any], _r: httpx.Request, n: str) -> httpx.Response:
        self.issues[int(n)]["body"] = body["body"]
        return httpx.Response(200, json={**self.issues[int(n)], "html_url": "u"})

    def _get_ref(self, _b: dict[str, Any], _r: httpx.Request, name: str) -> httpx.Response:
        if name not in self.refs:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200, json={"ref": f"refs/heads/{name}", "object": {"sha": self.refs[name]}}
        )

    def _create_ref(self, body: dict[str, Any], _r: httpx.Request) -> httpx.Response:
        name = body["ref"].removeprefix("refs/heads/")
        if name in self.refs:
            return httpx.Response(422, json={"message": "Reference already exists"})
        self.refs[name] = body["sha"]
        return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})

    def _open_pr(self, body: dict[str, Any], _r: httpx.Request) -> httpx.Response:
        for pr in self.pulls.values():
            if pr["head"]["ref"] == body["head"] and pr.get("state", "open") == "open":
                return httpx.Response(
                    422,
                    json={
                        "message": "Validation Failed",
                        "errors": [
                            {"message": f"A pull request already exists for acme:{body['head']}."}
                        ],
                    },
                )
        number = 100 + len(self.pulls)
        pr = {
            "number": number,
            "head": {"ref": body["head"], "sha": HEAD_SHA},
            "base": {"ref": body["base"]},
            "title": body["title"],
            "draft": body.get("draft", False),
            "html_url": f"https://github.com/acme/widgets/pull/{number}",
            "state": "open",
        }
        self.pulls[number] = pr
        return httpx.Response(201, json=pr)

    def _list_prs(self, _b: dict[str, Any], request: httpx.Request) -> httpx.Response:
        head = request.url.params.get("head", "").removeprefix("acme:")
        found = [
            {
                **pr,
                "html_url": pr.get(
                    "html_url", f"https://github.com/acme/widgets/pull/{pr['number']}"
                ),
            }
            for pr in self.pulls.values()
            if pr["head"]["ref"] == head
        ]
        return httpx.Response(200, json=found)

    def _merge(self, body: dict[str, Any], _r: httpx.Request, n: str) -> httpx.Response:
        pr = self.pulls[int(n)]
        if body.get("sha") and body["sha"] != pr["head"]["sha"]:
            return httpx.Response(
                409, json={"message": "Head branch was modified. Review and try the merge again."}
            )
        if not pr["mergeable"]:
            return httpx.Response(405, json={"message": "Pull Request is not mergeable"})
        pr["merged"] = True
        pr["merge_method"] = body.get("merge_method")
        return httpx.Response(
            200,
            json={"sha": "c" * 40, "merged": True, "message": "Pull Request successfully merged"},
        )


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


async def _run(gh: FakeGitHub, config: dict[str, Any], state: dict[str, Any] | None = None) -> Any:
    task = RuntimeTask(
        execution_id="exec-921",
        prompt_xml="<prompt/>",
        working_directory=".",
        runtime_config={"repo": "acme/widgets", **config, "__pipeline_state": state or {}},
        project_env_vars={"GH_TOKEN": TOKEN},
    )
    return await GithubAdapter(transport=httpx.MockTransport(gh)).execute(task)


def _delta(result: Any) -> dict[str, Any]:
    assert result.success, result.errors
    delta: dict[str, Any] = result.structured["state_delta"]
    return delta


# --------------------------------------------------------------------------- comment


async def test_comment_posts_the_templated_body_and_returns_its_id_and_url(gh: FakeGitHub) -> None:
    result = await _run(
        gh,
        {"op": "comment", "issue": 7, "body": "Plan for {{ state.extensions.topic }} is ready."},
        state={"extensions": {"topic": "caching"}},
    )

    assert _delta(result) == {
        "github_comment": {
            "id": 900,
            "url": "https://github.com/acme/widgets/issues/7#issuecomment-900",
            "issue": 7,
        }
    }
    assert gh.comments[0]["body"] == "Plan for caching is ready."


async def test_comment_needs_a_body(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "comment", "issue": 7})

    assert not result.success
    assert any("body" in e for e in result.errors), result.errors
    assert gh.requests == []


# --------------------------------------------------------------------------- update_issue_section


async def test_update_issue_section_replaces_only_the_marked_text(gh: FakeGitHub) -> None:
    result = await _run(
        gh, {"op": "update_issue_section", "issue": 7, "section": "plan", "content": "new plan"}
    )

    assert _delta(result)["github_issue_section"] == {
        "issue": 7,
        "section": "plan",
        "changed": True,
    }
    assert gh.issues[7]["body"] == (
        "Intro\n\n<!-- dap:section:plan -->\nnew plan\n<!-- /dap:section:plan -->\n\nOutro\n"
    )


async def test_update_issue_section_is_idempotent(gh: FakeGitHub) -> None:
    config = {"op": "update_issue_section", "issue": 7, "section": "plan", "content": "new plan"}
    await _run(gh, config)
    body_after_first = gh.issues[7]["body"]
    patches_after_first = sum(r.method == "PATCH" for r in gh.requests)

    second = await _run(gh, config)

    assert _delta(second)["github_issue_section"]["changed"] is False
    assert gh.issues[7]["body"] == body_after_first
    assert sum(r.method == "PATCH" for r in gh.requests) == patches_after_first  # no write


@pytest.mark.parametrize(
    "body",
    [
        "No markers at all\n",
        "<!-- dap:section:plan -->\nonly the opening marker\n",
        "<!-- dap:section:plan -->\na\n<!-- /dap:section:plan -->\n"
        "<!-- dap:section:plan -->\nb\n<!-- /dap:section:plan -->\n",
    ],
    ids=["missing", "unclosed", "duplicated"],
)
async def test_update_issue_section_refuses_missing_or_ambiguous_markers(
    gh: FakeGitHub, body: str
) -> None:
    gh.issues[7]["body"] = body

    result = await _run(
        gh, {"op": "update_issue_section", "issue": 7, "section": "plan", "content": "x"}
    )

    assert not result.success
    assert any("dap:section:plan" in e for e in result.errors), result.errors
    assert gh.issues[7]["body"] == body
    assert not any(r.method == "PATCH" for r in gh.requests)


async def test_update_issue_section_rejects_a_section_name_that_could_break_the_markers(
    gh: FakeGitHub,
) -> None:
    result = await _run(
        gh, {"op": "update_issue_section", "issue": 7, "section": "plan -->", "content": "x"}
    )

    assert not result.success
    assert any("section" in e for e in result.errors), result.errors
    assert gh.requests == []


# --------------------------------------------------------------------------- create_branch


async def test_create_branch_from_a_branch_name(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "create_branch", "branch": "feat/new", "base": "develop"})

    assert _delta(result)["github_branch"] == {
        "branch": "feat/new",
        "sha": BASE_SHA,
        "created": True,
    }
    assert gh.refs["feat/new"] == BASE_SHA


async def test_create_branch_from_a_sha_skips_the_ref_lookup(gh: FakeGitHub) -> None:
    sha = "d" * 40

    result = await _run(gh, {"op": "create_branch", "branch": "feat/at-sha", "base": sha})

    assert _delta(result)["github_branch"]["sha"] == sha
    assert not any("/git/ref/" in r.url.path for r in gh.requests)


async def test_create_branch_that_already_exists_at_that_sha_is_a_no_op(gh: FakeGitHub) -> None:
    gh.refs["feat/again"] = BASE_SHA

    result = await _run(gh, {"op": "create_branch", "branch": "feat/again", "base": "develop"})

    assert _delta(result)["github_branch"] == {
        "branch": "feat/again",
        "sha": BASE_SHA,
        "created": False,
    }


async def test_create_branch_refuses_a_branch_that_exists_elsewhere(gh: FakeGitHub) -> None:
    gh.refs["feat/taken"] = "e" * 40

    result = await _run(gh, {"op": "create_branch", "branch": "feat/taken", "base": "develop"})

    assert not result.success
    assert any("feat/taken" in e and "already exists" in e for e in result.errors), result.errors
    assert gh.refs["feat/taken"] == "e" * 40


async def test_create_branch_with_an_unknown_base_fails(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "create_branch", "branch": "feat/x2", "base": "nope"})

    assert not result.success
    assert any("nope" in e for e in result.errors), result.errors


# --------------------------------------------------------------------------- open_pr


async def test_open_pr_opens_a_draft_and_returns_number_and_url(gh: FakeGitHub) -> None:
    result = await _run(
        gh,
        {
            "op": "open_pr",
            "head": "feat/new",
            "base": "develop",
            "title": "Add {{ state.extensions.what }}",
            "body": "Closes #7",
            "draft": True,
        },
        state={"extensions": {"what": "caching"}},
    )

    opened = _delta(result)["github_opened_pr"]
    assert opened == {
        "number": 101,
        "url": "https://github.com/acme/widgets/pull/101",
        "created": True,
    }
    assert gh.pulls[101]["title"] == "Add caching"
    assert gh.pulls[101]["draft"] is True


async def test_open_pr_returns_the_existing_open_pr_for_that_head(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "open_pr", "head": "feat/x", "base": "develop", "title": "t"})

    assert _delta(result)["github_opened_pr"] == {
        "number": 12,
        "url": "https://github.com/acme/widgets/pull/12",
        "created": False,
    }
    assert len(gh.pulls) == 1


@pytest.mark.parametrize("missing", ["head", "base", "title"])
async def test_open_pr_requires_head_base_and_title(gh: FakeGitHub, missing: str) -> None:
    config = {"op": "open_pr", "head": "feat/new", "base": "develop", "title": "t"}
    del config[missing]

    result = await _run(gh, config)

    assert not result.success
    assert any(missing in e for e in result.errors), result.errors
    assert gh.requests == []


# --------------------------------------------------------------------------- merge_pr


async def test_merge_pr_squashes_by_default_when_the_head_matches(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "merge_pr", "pr": 12, "expected_head_sha": HEAD_SHA})

    assert _delta(result)["github_merge"] == {"number": 12, "merged": True, "sha": "c" * 40}
    assert gh.pulls[12]["merge_method"] == "squash"


async def test_merge_pr_refuses_when_the_head_moved(gh: FakeGitHub) -> None:
    result = await _run(gh, {"op": "merge_pr", "pr": 12, "expected_head_sha": "f" * 40})

    assert not result.success
    assert any("409" in e and "f" * 12 in e for e in result.errors), result.errors
    assert gh.pulls[12]["merged"] is False


async def test_merge_pr_requires_a_pinned_head(gh: FakeGitHub) -> None:
    """Merging whatever the head happens to be is exactly what the pin prevents."""
    result = await _run(gh, {"op": "merge_pr", "pr": 12})

    assert not result.success
    assert any("expected_head_sha" in e for e in result.errors), result.errors
    assert gh.requests == []


async def test_merge_pr_takes_the_pin_from_state(gh: FakeGitHub) -> None:
    state = {"extensions": {"github_pr": {"head": {"sha": HEAD_SHA}}}}

    result = await _run(
        gh,
        {
            "op": "merge_pr",
            "pr": 12,
            "expected_head_sha": "{{ state.extensions.github_pr.head.sha }}",
            "method": "rebase",
        },
        state=state,
    )

    assert _delta(result)["github_merge"]["merged"] is True
    assert gh.pulls[12]["merge_method"] == "rebase"


@pytest.mark.parametrize(
    ("config", "fragment"),
    [
        ({"method": "octopus"}, "method"),
        ({"expected_head_sha": "abc"}, "expected_head_sha"),
    ],
)
async def test_merge_pr_validates_method_and_sha(
    gh: FakeGitHub, config: dict[str, Any], fragment: str
) -> None:
    result = await _run(gh, {"op": "merge_pr", "pr": 12, "expected_head_sha": HEAD_SHA, **config})

    assert not result.success
    assert any(fragment in e for e in result.errors), result.errors
    assert gh.requests == []


async def test_merge_pr_reports_an_unmergeable_pr(gh: FakeGitHub) -> None:
    gh.pulls[12]["mergeable"] = False

    result = await _run(gh, {"op": "merge_pr", "pr": 12, "expected_head_sha": HEAD_SHA})

    assert not result.success
    assert any("405" in e for e in result.errors), result.errors


# --------------------------------------------------------------------------- token, every op


@pytest.mark.parametrize(
    "config",
    [
        {"op": "comment", "issue": 404, "body": "x"},
        {"op": "update_issue_section", "issue": 404, "section": "plan", "content": "x"},
        {"op": "create_branch", "branch": "feat/x3", "base": "missing"},
        {"op": "open_pr", "head": "feat/x", "base": "develop", "title": "t"},
        {"op": "merge_pr", "pr": 12, "expected_head_sha": "f" * 40},
    ],
    ids=["comment", "update_issue_section", "create_branch", "open_pr", "merge_pr"],
)
async def test_token_never_appears_in_any_write_result_or_logs(
    gh: FakeGitHub, config: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    result = await _run(gh, config)

    assert TOKEN not in result.model_dump_json()
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    "branch", ["feat?x=1", "feat#frag", "feat x", "../develop", "-flag", "a..b"]
)
async def test_branch_names_that_could_reshape_the_request_url_are_rejected(
    gh: FakeGitHub, branch: str
) -> None:
    result = await _run(gh, {"op": "create_branch", "branch": branch, "base": "develop"})

    assert not result.success
    assert any("branch" in e for e in result.errors), result.errors
    assert gh.requests == []


async def test_open_pr_accepts_a_cross_fork_owner_colon_branch_head(gh: FakeGitHub) -> None:
    result = await _run(
        gh, {"op": "open_pr", "head": "fork-owner:feat/x9", "base": "develop", "title": "t"}
    )

    assert _delta(result)["github_opened_pr"]["created"] is True
