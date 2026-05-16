"""Smoke tests for GET/POST /projects/{id}/workspace/* (#371)."""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    """Authed TestClient shim — see ``tests/smoke/conftest.py::authed_client``.

    Kept as a local alias because most test bodies in this file already
    take a ``client: TestClient`` parameter; renaming them all would
    bloat the X1 diff without changing behaviour.
    """
    return authed_client


def _create_project(
    client: TestClient,
    *,
    repo_url: str | None = "https://github.com/owner/repo.git",
    working_directory: str | None = None,
) -> str:
    resp = client.post(
        "/projects",
        json={
            "name": "ws-test",
            "description": "",
            "repo_url": repo_url,
            "default_branch": "main",
            "working_directory": working_directory,
            "env_vars": {},
            "pipelines": {},
        },
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


# ---------------------------------------------------------------------------
# GET /workspace/status
# ---------------------------------------------------------------------------


def test_workspace_status_no_repo_url_returns_not_exists(client: TestClient) -> None:
    pid = _create_project(client, repo_url=None)
    resp = client.get(f"/projects/{pid}/workspace/status")
    assert resp.status_code == 200
    assert resp.json()["exists"] is False
    assert resp.json()["path"] is None


def test_workspace_status_nonexistent_path_returns_not_exists(client: TestClient) -> None:
    pid = _create_project(client, working_directory="/tmp/definitely-does-not-exist-xyz")
    resp = client.get(f"/projects/{pid}/workspace/status")
    assert resp.status_code == 200
    assert resp.json()["exists"] is False


def test_workspace_status_invalid_repo_url_returns_422(client: TestClient) -> None:
    pid = _create_project(client, repo_url="https://evil.com/owner/repo.git")
    resp = client.get(f"/projects/{pid}/workspace/status")
    assert resp.status_code == 422
    assert "github.com" in resp.json()["detail"]


def test_workspace_status_real_git_repo(client: TestClient) -> None:
    """Status of a real local git repo returns exists=True with branch/commit."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        pid = _create_project(client, working_directory=tmp)
        resp = client.get(f"/projects/{pid}/workspace/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["branch"] is not None
        assert body["last_commit"] not in (None, "")


def test_workspace_status_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.get("/projects/00000000-0000-0000-0000-000000000000/workspace/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /workspace/init
# ---------------------------------------------------------------------------


def test_workspace_init_no_repo_url_returns_422(client: TestClient) -> None:
    pid = _create_project(client, repo_url=None)
    resp = client.post(f"/projects/{pid}/workspace/init")
    assert resp.status_code == 422
    assert "repo_url" in resp.json()["detail"]


def test_workspace_init_non_github_url_returns_422(client: TestClient) -> None:
    pid = _create_project(client, repo_url="https://gitlab.com/owner/repo.git")
    resp = client.post(f"/projects/{pid}/workspace/init")
    assert resp.status_code == 422
    assert "github.com" in resp.json()["detail"]


def test_workspace_init_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.post("/projects/00000000-0000-0000-0000-000000000000/workspace/init")
    assert resp.status_code == 404


def test_workspace_init_idempotent_if_already_exists(client: TestClient) -> None:
    """Init on an existing workspace returns initialized=False without re-cloning."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        pid = _create_project(client, working_directory=tmp)
        resp = client.post(f"/projects/{pid}/workspace/init")
        assert resp.status_code == 200
        assert resp.json()["initialized"] is False
        assert resp.json()["exists"] is True


# ---------------------------------------------------------------------------
# POST /workspace/sync
# ---------------------------------------------------------------------------


def test_workspace_sync_no_workspace_returns_422(client: TestClient) -> None:
    pid = _create_project(client, working_directory="/nonexistent/path")
    resp = client.post(f"/projects/{pid}/workspace/sync")
    assert resp.status_code == 422
    assert "init" in resp.json()["detail"].lower()
