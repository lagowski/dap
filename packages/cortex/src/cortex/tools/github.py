"""GitHub tools for agent nodes.

Each tool function takes a token parameter so nodes can only use the token
assigned to their role. The graph enforces which token each node gets.
"""

from __future__ import annotations

from datetime import UTC, datetime

from github import Auth, Github, GithubException
from langchain_core.tools import tool


def _get_client(token: str) -> Github:
    """Create a GitHub client with the given token."""
    return Github(auth=Auth.Token(token))


@tool
def read_issue(repo: str, issue_number: int, token: str) -> dict[str, str]:
    """Read a GitHub issue and return its details.

    Args:
        repo: Repository in owner/name format
        issue_number: Issue number
        token: GitHub token (read scope)

    Returns:
        Dict with title, body, state, labels, assignee
    """
    try:
        gh = _get_client(token)
        issue = gh.get_repo(repo).get_issue(issue_number)
        comments = [
            {
                "author": c.user.login,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
            }
            for c in issue.get_comments()
            if c.user and c.user.type == "User"
        ]
        return {
            "title": issue.title,
            "body": issue.body or "",
            "state": issue.state,
            "labels": ",".join(label.name for label in issue.labels),
            "assignee": issue.assignee.login if issue.assignee else "",
            "url": issue.html_url,
            "comments": comments,
        }
    except GithubException as e:
        return {"error": f"GitHub API error: {e.data.get('message', str(e))}"}


@tool
def read_repo_tree(repo: str, path: str, token: str) -> list[str]:
    """List files in a repository directory.

    Args:
        repo: Repository in owner/name format
        path: Directory path (empty string for root)
        token: GitHub token (read scope)

    Returns:
        List of file paths
    """
    try:
        gh = _get_client(token)
        contents = gh.get_repo(repo).get_contents(path)
        if not isinstance(contents, list):
            contents = [contents]
        return [c.path for c in contents]
    except GithubException as e:
        return [f"error: {e.data.get('message', str(e))}"]


@tool
def read_file(repo: str, path: str, token: str) -> str:
    """Read a file from the repository.

    Args:
        repo: Repository in owner/name format
        path: File path within the repo
        token: GitHub token (read scope)

    Returns:
        File content as string
    """
    try:
        gh = _get_client(token)
        content = gh.get_repo(repo).get_contents(path)
        if isinstance(content, list):
            return f"error: {path} is a directory"
        return content.decoded_content.decode("utf-8")
    except GithubException as e:
        return f"error: {e.data.get('message', str(e))}"


@tool
def update_issue_body(repo: str, issue_number: int, body: str, token: str) -> dict[str, str]:
    """Update a GitHub issue body.

    Args:
        repo: Repository in owner/name format
        issue_number: Issue number
        body: New complete issue body (markdown)
        token: GitHub token (issues scope)

    Returns:
        Dict with updated_at, issue_url, status (or error)
    """
    try:
        gh = _get_client(token)
        issue = gh.get_repo(repo).get_issue(issue_number)
        issue.edit(body=body)
        return {
            "updated_at": datetime.now(UTC).isoformat(),
            "issue_url": issue.html_url,
            "status": "success",
        }
    except GithubException as e:
        msg = e.data.get("message", str(e)) if isinstance(e.data, dict) else str(e)
        if e.status == 403:
            return {
                "error": (f"Permission denied (403): {msg}. Token lacks write access to {repo}."),
                "status_code": "403",
            }
        return {"error": f"GitHub API error: {msg}"}


@tool
def create_issue(repo: str, title: str, body: str, token: str) -> dict[str, str | int]:
    """Create a new GitHub issue.

    Args:
        repo: Repository in owner/name format
        title: Issue title
        body: Issue body (markdown)
        token: GitHub token (issues scope)

    Returns:
        Dict with number, url, status (or error)
    """
    try:
        gh = _get_client(token)
        issue = gh.get_repo(repo).create_issue(title=title, body=body)
        return {
            "number": issue.number,
            "url": issue.html_url,
            "status": "created",
        }
    except GithubException as e:
        return {"error": f"GitHub API error: {e.data.get('message', str(e))}"}


@tool
def create_issue_comment(repo: str, issue_number: int, body: str, token: str) -> str:
    """Add a comment to a GitHub issue.

    Args:
        repo: Repository in owner/name format
        issue_number: Issue number
        body: Comment body (markdown)
        token: GitHub token (issues scope)

    Returns:
        Comment URL or error
    """
    try:
        gh = _get_client(token)
        issue = gh.get_repo(repo).get_issue(issue_number)
        comment = issue.create_comment(body)
        return comment.html_url
    except GithubException as e:
        return f"error: {e.data.get('message', str(e))}"


@tool
def create_branch(repo: str, branch_name: str, base: str, token: str) -> str:
    """Create a new branch from a base branch — idempotent (issue #63).

    If the branch already exists it is force-reset to the current base SHA so
    stale commits from a prior failed run don't bleed into the new run (#159).

    Args:
        repo: Repository in owner/name format
        branch_name: New branch name
        base: Base branch (e.g., 'main')
        token: GitHub token (code scope)

    Returns:
        Branch ref or error string
    """
    try:
        gh = _get_client(token)
        r = gh.get_repo(repo)
        base_sha = r.get_git_ref(f"heads/{base}").object.sha
        try:
            existing = r.get_git_ref(f"heads/{branch_name}")
            # Force-reset to base SHA — clears stale commits from prior runs.
            if existing.object.sha != base_sha:
                existing.edit(base_sha, force=True)
            return f"refs/heads/{branch_name}"
        except GithubException as e:
            if e.status != 404:
                raise
            # 404 — branch doesn't exist, create it
        r.create_git_ref(f"refs/heads/{branch_name}", base_sha)
        return f"refs/heads/{branch_name}"
    except GithubException as e:
        return f"error: {e.data.get('message', str(e))}"


@tool
def create_pull_request(
    repo: str, title: str, body: str, head: str, base: str, token: str
) -> dict[str, str]:
    """Create a pull request.

    Args:
        repo: Repository in owner/name format
        title: PR title
        body: PR description
        head: Source branch
        base: Target branch
        token: GitHub token (code scope)

    Returns:
        Dict with number, url, state
    """
    try:
        gh = _get_client(token)
        pr = gh.get_repo(repo).create_pull(title=title, body=body, head=head, base=base)
        return {"number": str(pr.number), "url": pr.html_url, "state": pr.state}
    except GithubException as e:
        return {"error": f"GitHub API error: {e.data.get('message', str(e))}"}


def find_open_pr_for_branch(repo: str, branch: str, token: str) -> dict[str, int | str] | None:
    """Find an open PR whose head matches ``branch``. Returns
    ``{"number": int, "url": str}`` or ``None``.

    Used by ``pr_creator``'s recovery path (#139): when
    ``create_pull_request`` returns "Validation Failed" because a PR
    for the branch already exists (typically because claude_cli created
    it via Bash before pr_creator's Python code did), this helper
    locates the existing PR so its number/url can be captured into
    state. Without this, pr_creator drops the PR into a phantom failure
    and pr_merger refuses for lack of ``state.pr_number``.

    Returns ``None`` on GitHub API error — recovery is best-effort, and
    a failure here shouldn't crash pr_creator harder than the original
    failure already did.

    Not decorated as ``@tool`` because it's an internal helper called
    from a node, not exposed to LLM agents.
    """
    try:
        gh = _get_client(token)
        owner = repo.split("/", maxsplit=1)[0]
        # PyGithub's get_pulls(head=...) expects "owner:branch" format
        for pr in gh.get_repo(repo).get_pulls(state="open", head=f"{owner}:{branch}"):
            return {"number": pr.number, "url": pr.html_url}
        return None
    except GithubException:
        return None


@tool
def merge_pull_request(repo: str, pr_number: int, token: str) -> dict[str, str]:
    """Merge a pull request. Requires merge-scoped token (different user from PR author).

    Args:
        repo: Repository in owner/name format
        pr_number: PR number
        token: GitHub token (merge scope — MUST be different user from code token)

    Returns:
        Dict with merged status, sha
    """
    try:
        gh = _get_client(token)
        pr = gh.get_repo(repo).get_pull(pr_number)
        result = pr.merge(merge_method="squash")
        return {"merged": str(result.merged), "sha": result.sha, "message": result.message}
    except GithubException as e:
        return {"error": f"GitHub API error: {e.data.get('message', str(e))}"}
