"""The `github` runtime, read operations (#920, part of #810).

`read_issue` and `read_pr` against the GitHub REST API, with `repo` / `issue` / `pr`
templated from the pipeline state the engine injects as
``runtime_config["__pipeline_state"]``, and results returned through
``structured["state_delta"]`` like any other runtime.

HTTP goes through ``httpx.MockTransport``, so the adapter's real client code runs
(request building, auth header, pagination, error mapping) against canned GitHub
responses. ``test_github_adapter_live.py`` repeats the happy path against the real
API when a token is provided.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from dap_runtimes.adapters.github import GithubAdapter
from dap_runtimes.registry import create_default_registry
from dap_types import RuntimeTask

TOKEN = "ghp_SECRETtoken0123456789abcdefSECRET"
Handler = Callable[[httpx.Request], httpx.Response]

ISSUE = {
    "number": 7,
    "title": "Fix the thing",
    "body": "Steps to reproduce…",
    "state": "open",
    "html_url": "https://github.com/acme/widgets/issues/7",
    "labels": [{"name": "type:bug"}, {"name": "size:S"}],
    "user": {"login": "someone"},
}
PR = {
    "number": 12,
    "title": "Add the thing",
    "body": "Closes #7",
    "state": "open",
    "draft": False,
    "merged": False,
    "mergeable": True,
    "mergeable_state": "clean",
    "html_url": "https://github.com/acme/widgets/pull/12",
    "head": {"ref": "feat/thing", "sha": "a" * 40},
    "base": {"ref": "develop", "sha": "b" * 40},
}


class Recorder:
    """A MockTransport handler that serves canned routes and records every request."""

    def __init__(self, routes: dict[str, Handler | dict[str, Any] | list[Any]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if callable(route):
            return route(request)
        return httpx.Response(200, json=route)


def _adapter(recorder: Recorder) -> GithubAdapter:
    return GithubAdapter(transport=httpx.MockTransport(recorder))


def _task(
    config: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    project_env: dict[str, str] | None = None,
    instance_env: dict[str, str] | None = None,
) -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-920",
        prompt_xml="<prompt/>",
        working_directory=".",
        runtime_config={**config, "__pipeline_state": state or {}},
        project_env_vars={"GH_TOKEN": TOKEN} if project_env is None else project_env,
        instance_env_vars=instance_env or {},
    )


@pytest.fixture(autouse=True)
def _no_engine_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests decide where the token comes from; the developer's own shell must not leak in.
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "CORTEX_GH_TOKEN_READ"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- read_issue


async def test_read_issue_returns_the_issue_into_state() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/7": ISSUE})

    result = await _adapter(rec).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 7})
    )

    assert result.success, result.errors
    issue = result.structured["state_delta"]["github_issue"]  # type: ignore[index]
    assert issue == {
        "number": 7,
        "title": "Fix the thing",
        "body": "Steps to reproduce…",
        "state": "open",
        "labels": ["type:bug", "size:S"],
        "url": "https://github.com/acme/widgets/issues/7",
        "is_pull_request": False,
    }
    assert json.loads(result.output) == issue
    request = rec.requests[0]
    assert request.method == "GET"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


async def test_params_are_templated_from_pipeline_state() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/42": ISSUE | {"number": 42}})
    state = {"repo": "acme/widgets", "extensions": {"issue_number": 42}}

    result = await _adapter(rec).execute(
        _task(
            {
                "op": "read_issue",
                "repo": "{{ state.repo }}",
                "issue": "{{ state.extensions.issue_number }}",
            },
            state=state,
        )
    )

    assert result.success, result.errors
    assert rec.requests[0].url.path == "/repos/acme/widgets/issues/42"


async def test_reading_a_pr_number_as_an_issue_says_so() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/12": ISSUE | {"pull_request": {"url": "x"}}})

    result = await _adapter(rec).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 12})
    )

    assert result.success, result.errors
    assert result.structured["state_delta"]["github_issue"]["is_pull_request"] is True  # type: ignore[index]


async def test_state_key_is_configurable() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/7": ISSUE})

    result = await _adapter(rec).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 7, "state_key": "target"})
    )

    assert result.success, result.errors
    assert set(result.structured["state_delta"]) == {"target"}  # type: ignore[index]


# --------------------------------------------------------------------------- read_pr


def _paged_files(request: httpx.Request) -> httpx.Response:
    page = request.url.params.get("page", "1")
    if page == "1":
        next_url = "https://api.github.com/repos/acme/widgets/pulls/12/files?per_page=100&page=2"
        link = f'<{next_url}>; rel="next"'
        return httpx.Response(
            200, json=[{"filename": "a.py"}, {"filename": "b.py"}], headers={"Link": link}
        )
    return httpx.Response(200, json=[{"filename": "c.py"}])


async def test_read_pr_returns_refs_mergeability_and_every_changed_file() -> None:
    rec = Recorder(
        {"/repos/acme/widgets/pulls/12": PR, "/repos/acme/widgets/pulls/12/files": _paged_files}
    )

    result = await _adapter(rec).execute(_task({"op": "read_pr", "repo": "acme/widgets", "pr": 12}))

    assert result.success, result.errors
    pr = result.structured["state_delta"]["github_pr"]  # type: ignore[index]
    assert pr == {
        "number": 12,
        "title": "Add the thing",
        "body": "Closes #7",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "url": "https://github.com/acme/widgets/pull/12",
        "head": {"ref": "feat/thing", "sha": "a" * 40},
        "base": {"ref": "develop", "sha": "b" * 40},
        "files": ["a.py", "b.py", "c.py"],
    }
    pages = [r.url.params.get("page", "1") for r in rec.requests if r.url.path.endswith("/files")]
    assert pages == ["1", "2"]


async def test_pagination_never_follows_a_link_off_the_configured_api() -> None:
    """The token rides on every request, so a `next` link to another host must not be followed."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path.endswith("/files"):
            link = '<https://api.github.com.evil.example/steal?page=2>; rel="next"'
            return httpx.Response(200, json=[{"filename": "a.py"}], headers={"Link": link})
        return httpx.Response(200, json=PR)

    result = await GithubAdapter(transport=httpx.MockTransport(handler)).execute(
        _task({"op": "read_pr", "repo": "acme/widgets", "pr": 12})
    )

    assert not result.success
    assert any("pagination" in e for e in result.errors), result.errors
    assert set(hosts) == {"api.github.com"}


# --------------------------------------------------------------------------- token


async def test_token_env_names_the_variable_and_follows_env_layering() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/7": ISSUE})

    result = await _adapter(rec).execute(
        _task(
            {
                "op": "read_issue",
                "repo": "acme/widgets",
                "issue": 7,
                "token_env": "CORTEX_GH_TOKEN_READ",
            },
            project_env={},
            instance_env={"CORTEX_GH_TOKEN_READ": TOKEN},
        )
    )

    assert result.success, result.errors
    assert rec.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_agent_env_overrides_project_overrides_instance() -> None:
    rec = Recorder({"/repos/acme/widgets/issues/7": ISSUE})

    await _adapter(rec).execute(
        _task(
            {"op": "read_issue", "repo": "acme/widgets", "issue": 7, "env": {"GH_TOKEN": "agent"}},
            project_env={"GH_TOKEN": "project"},
            instance_env={"GH_TOKEN": "instance"},
        )
    )

    assert rec.requests[0].headers["Authorization"] == "Bearer agent"


async def test_missing_token_fails_without_calling_github() -> None:
    rec = Recorder({})

    result = await _adapter(rec).execute(
        _task(
            {"op": "read_issue", "repo": "acme/widgets", "issue": 7, "token_env": "NOPE_TOKEN"},
            project_env={},
        )
    )

    assert not result.success
    assert any("NOPE_TOKEN" in e for e in result.errors), result.errors
    assert rec.requests == []


async def test_token_never_appears_in_the_result_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def echo(request: httpx.Request) -> httpx.Response:
        # A hostile or buggy server echoing the auth header back in its error body.
        return httpx.Response(403, json={"message": f"bad auth {request.headers['Authorization']}"})

    rec = Recorder({"/repos/acme/widgets/issues/7": echo})

    result = await _adapter(rec).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 7})
    )

    assert not result.success
    assert TOKEN not in result.model_dump_json()
    assert TOKEN not in caplog.text


# --------------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    ("config", "fragment"),
    [
        ({"repo": "acme/widgets", "issue": 7}, "op"),
        ({"op": "delete_repo", "repo": "acme/widgets"}, "delete_repo"),
        ({"op": "read_issue", "issue": 7}, "repo"),
        ({"op": "read_issue", "repo": "acme/widgets"}, "issue"),
        ({"op": "read_pr", "repo": "acme/widgets"}, "pr"),
        ({"op": "read_issue", "repo": "not-a-repo", "issue": 7}, "owner/name"),
        ({"op": "read_issue", "repo": "acme/..", "issue": 7}, "owner/name"),
        ({"op": "read_issue", "repo": "../widgets", "issue": 7}, "owner/name"),
        ({"op": "read_issue", "repo": "acme/widgets", "issue": "seven"}, "issue"),
        ({"op": "read_issue", "repo": "acme/widgets", "issue": 0}, "issue"),
        ({"op": "read_issue", "repo": "{{ state.nope }}", "issue": 7}, "template"),
        ({"op": "read_issue", "repo": "acme/widgets", "issue": 7, "api_url": "http://x"}, "https"),
        ({"op": "read_issue", "repo": "acme/widgets", "issue": 7, "state_key": 5}, "state_key"),
    ],
)
async def test_bad_config_fails_without_calling_github(
    config: dict[str, Any], fragment: str
) -> None:
    rec = Recorder({})

    result = await _adapter(rec).execute(_task(config))

    assert not result.success
    assert any(fragment in e for e in result.errors), result.errors
    assert rec.requests == []


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "401"), (403, "403"), (404, "404"), (500, "500")],
)
async def test_github_errors_become_failed_results(status: int, fragment: str) -> None:
    rec = Recorder(
        {
            "/repos/acme/widgets/issues/7": lambda _r: httpx.Response(
                status, json={"message": "nope"}
            )
        }
    )

    result = await _adapter(rec).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 7})
    )

    assert not result.success
    assert any(fragment in e and "acme/widgets" in e for e in result.errors), result.errors


async def test_network_errors_become_failed_results() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await _adapter(Recorder({"/repos/acme/widgets/issues/7": boom})).execute(
        _task({"op": "read_issue", "repo": "acme/widgets", "issue": 7})
    )

    assert not result.success
    assert any("connection refused" in e for e in result.errors), result.errors


async def test_api_url_can_point_at_github_enterprise() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=ISSUE)

    adapter = GithubAdapter(transport=httpx.MockTransport(handler))
    result = await adapter.execute(
        _task(
            {
                "op": "read_issue",
                "repo": "acme/widgets",
                "issue": 7,
                "api_url": "https://ghe.example.com/api/v3",
            }
        )
    )

    assert result.success, result.errors
    assert seen == ["https://ghe.example.com/api/v3/repos/acme/widgets/issues/7"]


# --------------------------------------------------------------------------- registration


async def test_registered_as_an_always_available_http_runtime() -> None:
    adapter = create_default_registry().get("github")

    assert isinstance(adapter, GithubAdapter)
    assert adapter.kind == "http"
    assert (await adapter.healthcheck()).available
