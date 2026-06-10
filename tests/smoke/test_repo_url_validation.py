"""Unit tests for GitHub repo-URL owner/repo hardening (#778 audit security #2).

``_validate_github_url`` validated the host (SSRF guard) but the
regex-extracted owner/repo slug was unconstrained — ``..`` segments
could traverse the workspace path and a leading ``-`` could be parsed
as a flag by a git subprocess. The slug must be restricted to GitHub's
actual name charset before it reaches the filesystem or argv.
"""

from __future__ import annotations

import pytest
from dap_engine.api.projects import _validate_github_url
from fastapi import HTTPException


def _detail(exc_info: pytest.ExceptionInfo[HTTPException]) -> str:
    return str(exc_info.value.detail)


def test_accepts_canonical_https_url() -> None:
    assert (
        _validate_github_url("https://github.com/octo-org/repo.name_1.git")
        == "https://github.com/octo-org/repo.name_1.git"
    )


def test_accepts_ssh_form() -> None:
    assert _validate_github_url("git@github.com:owner/repo") == "https://github.com/owner/repo.git"


@pytest.mark.parametrize(
    "url",
    [
        # Path traversal into the workspace directory layout.
        "https://github.com/../etc",
        "https://github.com/owner/..",
        # Leading dash — argument injection into git subprocess argv.
        "https://github.com/-owner/repo",
        "https://github.com/owner/--upload-pack=evil",
        # Characters outside GitHub's slug charset.
        "https://github.com/owner/repo;rm -rf",
        "https://github.com/ow ner/repo",
    ],
)
def test_rejects_unsafe_owner_repo_slugs(url: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_github_url(url)
    assert exc_info.value.status_code == 422


def test_rejects_non_github_host() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_github_url("https://evil.example.com/owner/repo")
    assert exc_info.value.status_code == 422
    assert "github.com" in _detail(exc_info)
