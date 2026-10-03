"""The "read issue → comment" example bundle really works (#919, closes out #810).

``examples/pipelines/github-read-issue-comment.pipeline-bundle.json`` is two `github`
runtime nodes and nothing else. These tests import it through the real API and run it
through the real engine. Only GitHub itself is faked: the registered `github` adapter
gets an ``httpx.MockTransport`` serving one issue and accepting comments. That proves
the chain end to end: params templated from ``__pipeline_state``, the first node's
``state_delta`` landing in ``extensions``, and the second node rendering a comment
from it.

``test_bundle_against_a_real_scratch_issue`` repeats the run against real GitHub when
``DAP_GITHUB_IT_TOKEN``, ``DAP_GITHUB_IT_WRITE_REPO`` and ``DAP_GITHUB_IT_WRITE_ISSUE``
are set, and deletes the comment it posts.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes.adapters.github import GithubAdapter
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "pipelines"
    / "github-read-issue-comment.pipeline-bundle.json"
)
TOKEN = "ghp_SECRETtoken0123456789abcdefSECRET"
POLL_TIMEOUT_S = 10.0


class OneIssueGitHub:
    """Serves issue #7 of acme/widgets and records the comments posted to it."""

    def __init__(self) -> None:
        self.comments: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        if request.method == "GET" and path == "/repos/acme/widgets/issues/7":
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "Cache the dashboard",
                    "body": "It's slow.",
                    "state": "open",
                    "labels": [{"name": "type:feat"}, {"name": "area:dashboard"}],
                    "html_url": "https://github.com/acme/widgets/issues/7",
                },
            )
        if request.method == "POST" and path == "/repos/acme/widgets/issues/7/comments":
            self.comments.append(json.loads(request.content)["body"])
            return httpx.Response(
                201,
                json={
                    "id": 501,
                    "html_url": "https://github.com/acme/widgets/issues/7#issuecomment-501",
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GH_TOKEN", TOKEN)  # the engine-env layer of token resolution
    tmp = tempfile.mkdtemp(prefix="dap-gh-bundle-")
    app = create_app(
        EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
    )
    with authed_test_client(app) as c:
        yield c


def _import_and_run(client: TestClient, repo: str, issue: int) -> dict[str, Any]:
    imported = client.post("/pipelines/import", json=json.loads(BUNDLE.read_text()))
    assert imported.status_code == 201, imported.text
    pipeline_id = imported.json()["id"]
    started = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {
                "repo": repo,
                "branch": "main",
                "extensions": {"issue_number": issue},
            },
        },
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        run: dict[str, Any] = client.get(f"/runs/{run_id}").json()
        if run["final_status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {POLL_TIMEOUT_S}s")


def test_bundle_uses_only_the_github_runtime() -> None:
    bundle = json.loads(BUNDLE.read_text())
    agents = bundle["bundled_agents"]

    assert [n["agent_id"] in agents for n in bundle["pipeline"]["nodes"]] == [True, True]
    assert {a["runtime_id"] for a in agents.values()} == {"github"}
    # The last node can't set final_status, so the bundle must not demand one (#628).
    assert bundle["pipeline"]["defaults"]["requires_terminal_final_status"] is False
    # Tokens are referenced by env var name only.
    assert "ghp_" not in BUNDLE.read_text()


def test_bundle_runs_end_to_end_through_the_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = OneIssueGitHub()
    adapter = client.app.state.runtime_registry.get("github")  # type: ignore[attr-defined]
    assert isinstance(adapter, GithubAdapter)
    monkeypatch.setattr(adapter, "_transport", httpx.MockTransport(github))

    run = _import_and_run(client, "acme/widgets", 7)

    assert run["final_status"] == "success", run
    assert len(github.comments) == 1
    comment = github.comments[0]
    assert "Cache the dashboard" in comment
    assert "type:feat, area:dashboard" in comment
    assert TOKEN not in json.dumps(run)


@pytest.mark.skipif(
    not all(
        os.environ.get(k)
        for k in ("DAP_GITHUB_IT_TOKEN", "DAP_GITHUB_IT_WRITE_REPO", "DAP_GITHUB_IT_WRITE_ISSUE")
    ),
    reason="set DAP_GITHUB_IT_TOKEN, DAP_GITHUB_IT_WRITE_REPO and DAP_GITHUB_IT_WRITE_ISSUE",
)
def test_bundle_against_a_real_scratch_issue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = os.environ["DAP_GITHUB_IT_TOKEN"]
    repo = os.environ["DAP_GITHUB_IT_WRITE_REPO"]
    issue = int(os.environ["DAP_GITHUB_IT_WRITE_ISSUE"])
    monkeypatch.setenv("GH_TOKEN", token)
    api = f"https://api.github.com/repos/{repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(headers=headers, timeout=30) as gh:
        before = {c["id"] for c in gh.get(f"{api}/issues/{issue}/comments").json()}
        try:
            run = _import_and_run(client, repo, issue)
            assert run["final_status"] == "success", run
        finally:
            for c in gh.get(f"{api}/issues/{issue}/comments").json():
                if c["id"] not in before:
                    gh.delete(f"{api}/issues/comments/{c['id']}")
