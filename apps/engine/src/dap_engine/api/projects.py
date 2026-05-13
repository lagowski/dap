"""REST CRUD for projects (#63) + project-bound pipeline trigger (#66)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import subprocess
from typing import Any

import httpx
from dap_runtimes import RuntimeRegistry
from dap_types import Project, Run
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import (
    get_checkpointer,
    get_registry,
    get_run_registry,
    get_session,
    get_session_factory,
)
from dap_engine.api.runs import trigger_run
from dap_engine.api.schemas import (
    EnvVarValidationResult,
    ProjectRunRequest,
    ValidateEnvRequest,
    ValidateEnvResponse,
)
from dap_engine.auth.users import current_active_user
from dap_engine.contracts import ProjectCreate, ProjectUpdate, RunCreateRequest
from dap_engine.execution import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

_GH_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_")


def _is_github_token(value: str) -> bool:
    """Return True if *value* looks like a GitHub token."""
    return any(value.startswith(p) for p in _GH_TOKEN_PREFIXES)


@router.post("/validate-env", response_model=ValidateEnvResponse)
async def validate_env(
    payload: ValidateEnvRequest,
    _user: UserORM = Depends(current_active_user),
) -> ValidateEnvResponse:
    """Probe GitHub tokens among env vars — best-effort, never blocks save.

    Requires authentication: prevents anonymous token enumeration (#350).
    """
    results: list[EnvVarValidationResult] = []
    # Re-use a single client (one TLS connection pool) for all token probes.
    async with httpx.AsyncClient() as client:
        for key, value in payload.env_vars.items():
            if not _is_github_token(value):
                results.append(EnvVarValidationResult(key=key, is_token=False))
                continue
            # Token-shaped — validate against GitHub API.
            try:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {value}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=10.0,
                )
                if resp.status_code == httpx.codes.OK:
                    login = resp.json().get("login")
                    results.append(
                        EnvVarValidationResult(key=key, is_token=True, valid=True, login=login)
                    )
                else:
                    results.append(
                        EnvVarValidationResult(
                            key=key,
                            is_token=True,
                            valid=False,
                            error=f"GitHub API returned {resp.status_code}",
                        )
                    )
            except Exception as exc:
                logger.warning("Token validation failed for key %r: %s", key, exc)
                results.append(
                    EnvVarValidationResult(
                        key=key,
                        is_token=True,
                        valid=False,
                        error="Network error contacting GitHub",
                    )
                )
    return ValidateEnvResponse(results=results)


@router.get("")
def list_projects(
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
    archived: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items, total = repo.list_projects(
        session,
        actor_id=user.id,
        is_admin=user.is_superuser,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [p.model_dump(mode="json") for p in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Project)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.create_project(session, payload, user_id=user.id, is_admin=user.is_superuser)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.get_project(session, project_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Project:
    try:
        return repo.update_project(
            session, project_id, payload, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> Response:
    try:
        repo.archive_project(session, project_id, actor_id=user.id, is_admin=user.is_superuser)
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/issues")
async def list_project_issues(
    project_id: str,
    state: str = Query(default="open"),
    limit: int = Query(default=30, ge=1, le=100),
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> list[dict[str, Any]]:
    """Fetch GitHub issues for the project's linked repo (#368).

    Uses a GitHub token from the project's env_vars if present,
    otherwise falls back to the GITHUB_TOKEN engine env var.
    Returns a lightweight list suitable for an issue picker UI.
    """
    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not project.repo_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Project has no repo_url configured",
        )

    # Extract owner/repo from repo_url (https or git@)
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", project.repo_url)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot parse owner/repo from repo_url: {project.repo_url}",
        )
    owner_repo = match.group(1)

    # Pick the first GitHub token from project env_vars, fall back to engine env.
    gh_token = next(
        (v for v in (project.env_vars or {}).values() if _is_github_token(v)),
        os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
    )
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner_repo}/issues",
                params={"state": state, "per_page": limit, "sort": "updated"},
                headers=headers,
                timeout=10.0,
            )
        except Exception as exc:
            logger.warning("GitHub issues fetch failed for %s: %s", owner_repo, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to reach GitHub API",
            ) from exc

    if resp.status_code == httpx.codes.NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub repo not found: {owner_repo}",
        )
    if resp.status_code != httpx.codes.OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub API returned {resp.status_code}",
        )

    issues = resp.json()
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "body": (i.get("body") or "")[:500],
            "state": i["state"],
            "url": i["html_url"],
            "labels": [lb["name"] for lb in i.get("labels", [])],
            "created_at": i["created_at"],
            "updated_at": i["updated_at"],
        }
        for i in issues
        if not i.get("pull_request")  # exclude PRs which appear in issues API
    ]


def _workspace_path(repo_url: str) -> str:
    """Return the local workspace path for a repo_url.

    Mirrors the cortex CLI convention:
    ``~/.cortex/projects/{owner}-{repo}/repo``
    """
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", repo_url)
    if not match:
        raise ValueError(f"Cannot parse owner/repo from repo_url: {repo_url!r}")
    owner, repo_name = match.group(1), match.group(2)
    base = os.path.expanduser(f"~/.cortex/projects/{owner}-{repo_name}")
    return str(os.path.join(base, "repo"))


def _clone_token(project: Project) -> str | None:
    """Return first GitHub token from project env_vars, else engine env."""
    token = next(
        (v for v in (project.env_vars or {}).values() if _is_github_token(v)),
        None,
    )
    return token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _workspace_status(workspace: str) -> dict[str, Any]:
    """Collect git status for an existing workspace clone."""

    def git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=workspace, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            return ""

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    last = git("log", "-1", "--format=%h %s")
    clean = git("status", "--porcelain") == ""
    return {
        "exists": True,
        "path": workspace,
        "branch": branch or "unknown",
        "clean": clean,
        "last_commit": last or "—",
    }


@router.get("/{project_id}/workspace/status")
def get_workspace_status(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> dict[str, Any]:
    """Return the local git workspace status for a project (#371)."""
    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Determine workspace path
    workspace = project.working_directory
    if not workspace and project.repo_url:
        with contextlib.suppress(ValueError):
            workspace = _workspace_path(project.repo_url)

    if not workspace:
        return {"exists": False, "path": None, "branch": None, "clean": None, "last_commit": None}

    if not os.path.isdir(os.path.join(workspace, ".git")):
        return {
            "exists": False,
            "path": workspace,
            "branch": None,
            "clean": None,
            "last_commit": None,
        }

    return _workspace_status(workspace)


@router.post("/{project_id}/workspace/init")
async def init_workspace(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> dict[str, Any]:
    """Clone the project repo locally and set working_directory (#371).

    Uses the first GitHub token found in project env_vars, falling back
    to the GITHUB_TOKEN / GH_TOKEN engine environment variable.
    """
    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not project.repo_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Project has no repo_url configured",
        )

    try:
        workspace = _workspace_path(project.repo_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # Already initialised — just return current status.
    if os.path.isdir(os.path.join(workspace, ".git")):
        return {**_workspace_status(workspace), "initialized": False}

    token = _clone_token(project)
    if token:
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", project.repo_url)
        slug = m.group(1) if m else None
        clone_url = (
            f"https://x-access-token:{token}@github.com/{slug}.git" if slug else project.repo_url
        )
    else:
        clone_url = project.repo_url

    os.makedirs(os.path.dirname(workspace), exist_ok=True)

    proc = await asyncio.create_subprocess_exec(  # type: ignore[attr-defined]
        "git",
        "clone",
        clone_url,
        workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").replace(token or "", "<token>")[:300]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"git clone failed: {err}",
        )

    # Persist working_directory on the project row.
    repo.update_project(
        session,
        project_id,
        ProjectUpdate(
            name=project.name,
            description=project.description,
            working_directory=workspace,
            repo_url=project.repo_url,
            default_branch=project.default_branch,
            pipelines=project.pipelines,
            env_vars=project.env_vars,
        ),
        actor_id=user.id,
        is_admin=user.is_superuser,
    )
    logger.info("workspace initialised: %s", workspace)
    return {**_workspace_status(workspace), "initialized": True}


@router.post("/{project_id}/workspace/sync")
async def sync_workspace(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserORM = Depends(current_active_user),
) -> dict[str, Any]:
    """git fetch + checkout default_branch + pull on an existing workspace (#371)."""

    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    workspace = project.working_directory
    if not workspace or not os.path.isdir(os.path.join(workspace, ".git")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Workspace not initialised — call /workspace/init first",
        )

    branch = project.default_branch or "main"

    async def run_git(*args: str) -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec(  # type: ignore[attr-defined]
            "git",
            *args,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(p.communicate(), timeout=60)
        return p.returncode or 0, err.decode("utf-8", errors="replace")[:200]

    for cmd in (
        ("fetch", "--prune", "origin"),
        ("checkout", branch),
        ("pull", "--ff-only", "origin", branch),
    ):
        rc, err = await run_git(*cmd)
        if rc != 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"git {cmd[0]} failed: {err}",
            )

    return _workspace_status(workspace)


@router.post(
    "/{project_id}/run/{kind}",
    status_code=status.HTTP_201_CREATED,
    response_model=Run,
)
async def trigger_project_run(
    project_id: str,
    kind: str,
    payload: ProjectRunRequest | None = None,
    session: Session = Depends(get_session),
    registry: RuntimeRegistry = Depends(get_registry),
    run_registry: RunRegistry = Depends(get_run_registry),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    user: UserORM = Depends(current_active_user),
) -> Run:
    """Trigger the project's bound pipeline for a given workflow kind (#66).

    Convenience over POST /runs: looks up ``pipeline_id =
    project.pipelines[kind]``, fills the engine's ``project_id`` slot,
    seeds ``repo`` / ``branch`` from the project (overridable per
    call), and delegates to the standard run-trigger flow.

    Ownership: non-admins can only trigger projects they own. The
    project lookup is gated by ``repo.get_project`` — same gate the
    other CRUD endpoints use — so a cross-user probe surfaces 404
    (anti-enumeration), never the structured "Project is archived"
    422 that would leak existence. Sharing the gate avoids drift if
    the ownership rule changes (Copilot review on PR #312).
    """
    try:
        project = repo.get_project(
            session, project_id, actor_id=user.id, is_admin=user.is_superuser
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if project.archived_at is not None:
        # 422 — same status as POST /runs returns when an archived project_id
        # is in the payload. Aligning the two trigger paths so clients can
        # share error-handling logic regardless of which endpoint they used. (#205)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Project is archived: {project_id}",
        )

    bound_pipeline_id = project.pipelines.get(kind)
    if not bound_pipeline_id:
        bound_kinds = sorted(project.pipelines)
        bound_msg = ", ".join(bound_kinds) if bound_kinds else "(none)"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Project '{project_id}' has no pipeline bound to kind "
                f"'{kind}'. Bound kinds: {bound_msg}"
            ),
        )

    body = payload or ProjectRunRequest()
    run_request = RunCreateRequest(
        pipeline_id=bound_pipeline_id,
        pipeline_version=body.pipeline_version,
        project_id=project_id,
        initial_state=body.initial_state,
    )
    # Delegate to the existing trigger flow — same validation, same
    # background-task spawn, same Run shape on the way back. The
    # archived-project check is duplicated there but unreachable
    # because we already 422'd above.
    return await trigger_run(
        payload=run_request,
        session=session,
        registry=registry,
        run_registry=run_registry,
        session_factory=session_factory,
        checkpointer=checkpointer,
        user=user,
    )
