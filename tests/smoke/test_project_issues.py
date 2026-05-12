"""Tests for GET /projects/{id}/issues (#368)."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-project-issues-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


_DEFAULT_REPO_URL = "https://github.com/org/repo.git"


def _create_project(client: TestClient, *, repo_url: str | None = _DEFAULT_REPO_URL) -> str:
    resp = client.post(
        "/projects",
        json={
            "name": "test-project",
            "description": "",
            "repo_url": repo_url,
            "default_branch": "main",
            "working_directory": None,
            "env_vars": {},
            "pipelines": {},
        },
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _mock_github(
    *,
    status_code: int = 200,
    json_body: list[dict[str, Any]] | None = None,
    side_effect: Exception | None = None,
) -> Any:
    mock_instance = AsyncMock()
    if side_effect is not None:
        mock_instance.get = AsyncMock(side_effect=side_effect)
    else:
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_body or []
        mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("dap_engine.api.projects.httpx.AsyncClient", return_value=mock_instance)


_SAMPLE_ISSUES = [
    {
        "number": 9,
        "title": "fix: NetworkError on dashboard refresh",
        "body": "Long description here.",
        "state": "open",
        "html_url": "https://github.com/org/repo/issues/9",
        "labels": [{"name": "bug"}],
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-12T18:00:00Z",
        "pull_request": None,  # not a PR
    },
    {
        "number": 8,
        "title": "feat: keyboard shortcut toggle",
        "body": "Add Ctrl+D shortcut.",
        "state": "open",
        "html_url": "https://github.com/org/repo/issues/8",
        "labels": [],
        "created_at": "2026-05-01T09:00:00Z",
        "updated_at": "2026-05-10T12:00:00Z",
    },
]


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_list_issues_returns_open_issues(client: TestClient) -> None:
    project_id = _create_project(client)
    with _mock_github(json_body=_SAMPLE_ISSUES):
        resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["number"] == 9
    assert data[0]["title"] == "fix: NetworkError on dashboard refresh"
    assert data[0]["labels"] == ["bug"]
    assert data[0]["url"] == "https://github.com/org/repo/issues/9"


def test_list_issues_excludes_pull_requests(client: TestClient) -> None:
    """PRs appear in the GitHub issues API but must be filtered out."""
    issues_with_pr = [
        *_SAMPLE_ISSUES,
        {
            "number": 7,
            "title": "PR: add feature",
            "body": "",
            "state": "open",
            "html_url": "https://github.com/org/repo/pull/7",
            "labels": [],
            "created_at": "2026-05-01T08:00:00Z",
            "updated_at": "2026-05-01T08:00:00Z",
            "pull_request": {"url": "https://api.github.com/repos/org/repo/pulls/7"},
        },
    ]
    project_id = _create_project(client)
    with _mock_github(json_body=issues_with_pr):
        resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 200
    numbers = [i["number"] for i in resp.json()]
    assert 7 not in numbers


# ---------------------------------------------------------------------------
# Missing or unparseable repo_url → 422
# ---------------------------------------------------------------------------


def test_list_issues_no_repo_url_returns_422(client: TestClient) -> None:
    project_id = _create_project(client, repo_url=None)
    resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 422
    assert "repo_url" in resp.json()["detail"]


def test_list_issues_bad_repo_url_returns_422(client: TestClient) -> None:
    project_id = _create_project(client, repo_url="not-a-url")
    resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 422
    assert "Cannot parse" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GitHub API errors → 502
# ---------------------------------------------------------------------------


def test_list_issues_github_404_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    with _mock_github(status_code=404):
        resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_list_issues_github_error_returns_502(client: TestClient) -> None:
    project_id = _create_project(client)
    with _mock_github(status_code=500):
        resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 502


def test_list_issues_network_error_returns_502(client: TestClient) -> None:
    project_id = _create_project(client)
    with _mock_github(side_effect=Exception("connection refused")):
        resp = client.get(f"/projects/{project_id}/issues")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Unknown project → 404
# ---------------------------------------------------------------------------


def test_list_issues_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.get("/projects/00000000-0000-0000-0000-000000000000/issues")
    assert resp.status_code == 404
