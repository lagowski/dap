"""The ``github`` runtime: declarative GitHub operations (#920, #921; part of #810).

Read operations:

- ``read_issue`` (``issue``) → title, body, state, label names, URL, and whether the
  number is actually a PR.
- ``read_pr`` (``pr``) → title, body, state, draft/merged, mergeability, head/base ref
  and sha, URL, and every changed file name (paginated).

Write operations:

- ``comment`` (``issue``, ``body``) → posts a comment on an issue or PR.
- ``update_issue_section`` (``issue``, ``section``, ``content``) → replaces the text
  between ``<!-- dap:section:<section> -->`` and ``<!-- /dap:section:<section> -->``.
  Idempotent: if the text is already there, nothing is written. Missing, unclosed or
  duplicated markers fail rather than appending blindly.
- ``create_branch`` (``branch``, ``base``) → creates ``branch`` at ``base`` (a branch
  name or a full sha). Already existing at that sha is a no-op; existing elsewhere
  fails.
- ``open_pr`` (``head``, ``base``, ``title``, optional ``body`` and ``draft``) → opens a
  PR, or returns the open PR that already exists for ``head``.
- ``merge_pr`` (``pr``, ``expected_head_sha``, optional ``method``: squash (default),
  merge or rebase) → merges, but only if the PR's head is still exactly
  ``expected_head_sha`` (GitHub checks it atomically and answers 409 otherwise). The pin
  is required: merging whatever the head happens to be is what it exists to prevent.
  ``read_pr`` puts the sha in state (``{{ state.extensions.github_pr.head.sha }}``).

Each op returns the affected resource's number, URL or sha into state.

Config:

- Every string param may be a literal or a Jinja template rendered against
  ``{"state": <pipeline state>}``. The engine injects that state into every task as
  ``runtime_config["__pipeline_state"]``. Sandboxed, with ``StrictUndefined`` (as in
  the ``http`` runtime), so a typo fails the node instead of calling GitHub with an
  empty value. Every param is validated before any request is made.
- ``token_env``: name of the env var holding the token (default ``GH_TOKEN``), resolved
  through DAP's env layering: engine env < instance env vars < project env vars <
  ``runtime_config.env``. Role-separated tokens (``CORTEX_GH_TOKEN_READ``,
  ``…_ISSUES``, ``…_CODE``, ``…_MERGE``) plug in by name. The token is only ever sent
  as an ``Authorization`` header and is scrubbed from every message.
- ``state_key``: where the result lands in state (defaults per op, below). It is
  returned as ``structured["state_delta"]``, which the engine merges like any other
  runtime's (unknown keys go to ``extensions``).
- ``api_url``: defaults to ``https://api.github.com``; set it for GitHub Enterprise.
  Must be https. Pagination links are only followed on that same base URL, so the
  token is never sent anywhere else.

Every failure (bad config, missing token, a 4xx/5xx, a network error) returns a failed
``RuntimeResult``; nothing raises.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Final

import httpx
from dap_types import HealthStatus, OutputCallback, RuntimeKind, RuntimeResult, RuntimeTask
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from dap_runtimes.adapters._subprocess_env import merge_subprocess_env
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.github")

DEFAULT_API_URL: Final = "https://api.github.com"
DEFAULT_TOKEN_ENV: Final = "GH_TOKEN"
API_VERSION: Final = "2022-11-28"
MS_PER_SECOND: Final = 1000
PER_PAGE: Final = 100
# GitHub lists at most 3000 files per PR; 30 pages of 100 covers that.
MAX_FILE_PAGES: Final = 30
MESSAGE_PREVIEW: Final = 200
HTTP_ERROR: Final = 400
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
HTTP_NOT_FOUND: Final = 404
HTTP_UNPROCESSABLE: Final = 422
_REPO_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_SECTION_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+$")
# Branch names go into URL paths (git/ref/heads/<name>), so only characters that can't
# reshape the URL: no "?", "#", "%" or spaces. ":" allows a cross-fork "owner:branch" head.
_REF_RE: Final = re.compile(r"^[A-Za-z0-9._/:-]+$")
_MERGE_METHODS: Final = ("squash", "merge", "rebase")


class _OpSpec:
    def __init__(
        self, required: dict[str, str], state_key: str, optional: dict[str, str] | None = None
    ) -> None:
        self.required = required
        self.optional = optional or {}
        self.state_key = state_key


# Param kinds: number | text | content (may be empty) | ref | section | sha | bool | method
_OPS: Final[dict[str, _OpSpec]] = {
    "read_issue": _OpSpec({"issue": "number"}, "github_issue"),
    "read_pr": _OpSpec({"pr": "number"}, "github_pr"),
    "comment": _OpSpec({"issue": "number", "body": "text"}, "github_comment"),
    "update_issue_section": _OpSpec(
        {"issue": "number", "section": "section", "content": "content"}, "github_issue_section"
    ),
    "create_branch": _OpSpec({"branch": "ref", "base": "ref"}, "github_branch"),
    "open_pr": _OpSpec(
        {"head": "ref", "base": "ref", "title": "text"},
        "github_opened_pr",
        optional={"body": "content", "draft": "bool"},
    ),
    "merge_pr": _OpSpec(
        {"pr": "number", "expected_head_sha": "sha"}, "github_merge", optional={"method": "method"}
    ),
}


class _Fail(Exception):
    """Raised inside execute() to short-circuit to a failed result."""


class GithubAdapter(BaseAdapter):
    """GitHub REST operations as a pipeline node."""

    id = "github"
    display_name = "GitHub"
    kind: RuntimeKind = "http"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # Injectable so tests run the real client code over httpx.MockTransport.
        self._transport = transport

    async def healthcheck(self) -> HealthStatus:
        """Always available: repo access and the token are checked per call."""
        return HealthStatus(available=True, version=f"httpx {httpx.__version__}")

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        # on_output ignored: nothing streams from a REST call.
        del on_output
        start = time.monotonic()
        token: str | None = None
        try:
            call = _Call.from_config(task.runtime_config)
            token = _resolve_token(task, call.token_env)
            data = await self._run(call, token, task.timeout_ms)
        except _Fail as exc:
            return _failed(_redact(str(exc), token), start)

        logger.info("github %s %s %s", call.op, call.repo, call.target)
        return RuntimeResult(
            success=True,
            output=json.dumps(data, ensure_ascii=False),
            duration_ms=_elapsed_ms(start),
            structured={
                "state_delta": {call.state_key: data},
                "github": {"op": call.op, "repo": call.repo, "target": call.target},
            },
        )

    async def _run(self, call: _Call, token: str, timeout_ms: int | None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        timeout = max(timeout_ms or 60_000, 1) / MS_PER_SECOND
        async with httpx.AsyncClient(
            transport=self._transport, headers=headers, timeout=timeout
        ) as client:
            return await _HANDLERS[call.op](_Api(client, call), call.params)


class _Call:
    """A validated, rendered operation."""

    def __init__(
        self,
        *,
        op: str,
        repo: str,
        params: dict[str, Any],
        state_key: str,
        token_env: str,
        api_url: str,
    ) -> None:
        self.op = op
        self.repo = repo
        self.params = params
        self.state_key = state_key
        self.token_env = token_env
        self.api_url = api_url

    @property
    def target(self) -> Any:
        """What the op acts on, for logs and ``structured["github"]``."""
        for key in ("issue", "pr", "branch", "head"):
            if key in self.params:
                return self.params[key]
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> _Call:
        op = config.get("op")
        if not isinstance(op, str) or not op:
            raise _Fail(f"runtime_config.op is required (one of: {', '.join(_OPS)})")
        if op not in _OPS:
            raise _Fail(f"runtime_config.op {op!r} is not supported (one of: {', '.join(_OPS)})")
        spec = _OPS[op]

        state = config.get("__pipeline_state") or {}
        repo = _render(config, "repo", state)
        # "." / ".." segments would let the request path climb out of /repos/<owner>/<name>.
        if (
            not isinstance(repo, str)
            or not _REPO_RE.match(repo)
            or any(part in {".", ".."} for part in repo.split("/"))
        ):
            raise _Fail(f"runtime_config.repo must be owner/name, got {repo!r}")

        params: dict[str, Any] = {}
        for key, kind in spec.required.items():
            params[key] = _param(config, key, kind, state, op=op)
        for key, kind in spec.optional.items():
            if config.get(key) is not None:
                params[key] = _param(config, key, kind, state, op=op)

        state_key = config.get("state_key", spec.state_key)
        if not isinstance(state_key, str) or not state_key.strip():
            raise _Fail("runtime_config.state_key must be a non-empty string")
        token_env = config.get("token_env") or DEFAULT_TOKEN_ENV
        if not isinstance(token_env, str):
            raise _Fail("runtime_config.token_env must be the name of an env var")
        api_url = config.get("api_url") or DEFAULT_API_URL
        if not isinstance(api_url, str) or not api_url.startswith("https://"):
            raise _Fail(f"runtime_config.api_url must be an https URL, got {api_url!r}")

        return cls(
            op=op,
            repo=repo,
            params=params,
            state_key=state_key.strip(),
            token_env=token_env,
            api_url=api_url.rstrip("/"),
        )


class _Api:
    """Requests bound to one repo, with GitHub's errors mapped to messages."""

    def __init__(self, client: httpx.AsyncClient, call: _Call) -> None:
        self._client = client
        self._call = call
        self._base = f"{call.api_url}/repos/{call.repo}"

    @property
    def repo(self) -> str:
        return self._call.repo

    @property
    def owner(self) -> str:
        return self._call.repo.split("/", 1)[0]

    async def get(self, path: str) -> dict[str, Any]:
        return _object(await self.request("GET", path), path)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow: frozenset[int] = frozenset(),
    ) -> httpx.Response:
        """Send a request; a 4xx/5xx raises ``_Fail`` unless its status is in ``allow``."""
        return await self._send(method, f"{self._base}/{path}", json_body, params, allow)

    async def files(self, pr: int) -> list[str]:
        url: str | None = f"{self._base}/pulls/{pr}/files"
        params: dict[str, Any] | None = {"per_page": PER_PAGE}
        names: list[str] = []
        for _ in range(MAX_FILE_PAGES):
            if url is None:
                break
            response = await self._send("GET", url, None, params, frozenset())
            page = _json(response)
            if not isinstance(page, list):
                raise _Fail(f"GitHub returned an unexpected file list for {self._call.repo}")
            names.extend(str(f.get("filename")) for f in page if isinstance(f, dict))
            url = response.links.get("next", {}).get("url")
            params = None  # the next link already carries them
            # Never follow a link off the configured API: it would carry the token.
            if url is not None and not url.startswith(self._call.api_url + "/"):
                raise _Fail(f"GitHub returned a pagination link outside {self._call.api_url}")
        return names

    async def _send(
        self,
        method: str,
        url: str,
        json_body: dict[str, Any] | None,
        params: dict[str, Any] | None,
        allow: frozenset[int],
    ) -> httpx.Response:
        where = url.removeprefix(self._call.api_url + "/")
        try:
            response = await self._client.request(method, url, json=json_body, params=params)
        except httpx.TimeoutException as exc:
            raise _Fail(f"GitHub request timed out for {where}") from exc
        except httpx.RequestError as exc:
            raise _Fail(f"GitHub request failed for {where}: {exc}") from exc
        if response.status_code >= HTTP_ERROR and response.status_code not in allow:
            raise _Fail(_status_message(response, where, self._call.repo))
        return response


# --------------------------------------------------------------------------- op handlers


async def _read_issue(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    return _issue(await api.get(f"issues/{p['issue']}"))


async def _read_pr(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    pr = await api.get(f"pulls/{p['pr']}")
    return {**_pr(pr), "files": await api.files(p["pr"])}


async def _comment(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    path = f"issues/{p['issue']}/comments"
    created = _object(await api.request("POST", path, json_body={"body": p["body"]}), path)
    return {"id": created.get("id"), "url": created.get("html_url"), "issue": p["issue"]}


async def _update_issue_section(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    issue, section = p["issue"], p["section"]
    body = (await api.get(f"issues/{issue}")).get("body") or ""
    updated = _replace_section(body, section, p["content"], issue)
    changed = updated != body
    if changed:
        await api.request("PATCH", f"issues/{issue}", json_body={"body": updated})
    return {"issue": issue, "section": section, "changed": changed}


async def _create_branch(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    branch, base = p["branch"], p["base"]
    sha = base if _SHA_RE.match(base) else await _ref_sha(api, base, role="base")
    response = await api.request(
        "POST",
        "git/refs",
        json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        allow=frozenset({HTTP_UNPROCESSABLE}),
    )
    if response.status_code != HTTP_UNPROCESSABLE:
        return {"branch": branch, "sha": sha, "created": True}
    # 422 "Reference already exists": fine if it already points where we wanted.
    existing = await _ref_sha(api, branch, role="branch")
    if existing != sha:
        raise _Fail(
            f"branch {branch} already exists at {existing[:12]}, not at {base} ({sha[:12]})"
        )
    return {"branch": branch, "sha": sha, "created": False}


async def _open_pr(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"head": p["head"], "base": p["base"], "title": p["title"]}
    if "body" in p:
        payload["body"] = p["body"]
    if "draft" in p:
        payload["draft"] = p["draft"]
    response = await api.request(
        "POST", "pulls", json_body=payload, allow=frozenset({HTTP_UNPROCESSABLE})
    )
    if response.status_code != HTTP_UNPROCESSABLE:
        pr = _object(response, "pulls")
        return {"number": pr.get("number"), "url": pr.get("html_url"), "created": True}
    if "already exists" not in _error_details(response):
        raise _Fail(_status_message(response, "pulls", api.repo))
    # An open PR for this head already exists: return it instead of failing.
    head = p["head"] if ":" in p["head"] else f"{api.owner}:{p['head']}"
    listing = _json(await api.request("GET", "pulls", params={"head": head, "state": "open"}))
    if not isinstance(listing, list) or not listing:
        raise _Fail(f"GitHub says a PR for {p['head']} exists but did not list it")
    pr = listing[0]
    return {"number": pr.get("number"), "url": pr.get("html_url"), "created": False}


async def _merge_pr(api: _Api, p: dict[str, Any]) -> dict[str, Any]:
    pr, sha = p["pr"], p["expected_head_sha"]
    payload = {"merge_method": p.get("method", "squash"), "sha": sha}
    path = f"pulls/{pr}/merge"
    try:
        merged = _object(await api.request("PUT", path, json_body=payload), path)
    except _Fail as exc:
        raise _Fail(f"{exc} (expected head {sha[:12]})") from exc
    return {"number": pr, "merged": bool(merged.get("merged")), "sha": merged.get("sha")}


_HANDLERS: Final = {
    "read_issue": _read_issue,
    "read_pr": _read_pr,
    "comment": _comment,
    "update_issue_section": _update_issue_section,
    "create_branch": _create_branch,
    "open_pr": _open_pr,
    "merge_pr": _merge_pr,
}


async def _ref_sha(api: _Api, name: str, *, role: str) -> str:
    response = await api.request("GET", f"git/ref/heads/{name}", allow=frozenset({HTTP_NOT_FOUND}))
    if response.status_code == HTTP_NOT_FOUND:
        raise _Fail(f"{role} {name!r} not found in {api.repo}")
    ref = _object(response, f"git/ref/heads/{name}")
    sha = (ref.get("object") or {}).get("sha")
    if not isinstance(sha, str):
        raise _Fail(f"GitHub returned no sha for {role} {name!r}")
    return sha


def _replace_section(body: str, section: str, content: str, issue: int) -> str:
    opening = f"<!-- dap:section:{section} -->"
    closing = f"<!-- /dap:section:{section} -->"
    start, end = body.find(opening), body.find(closing)
    if body.count(opening) != 1 or body.count(closing) != 1 or end < start:
        raise _Fail(
            f"issue #{issue} must contain exactly one {opening} … {closing} pair; "
            "refusing to write without it"
        )
    return body[: start + len(opening)] + f"\n{content}\n" + body[end:]


def _render(config: dict[str, Any], key: str, state: Any) -> Any:
    value = config.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _Fail(f"runtime_config.{key} is required")
    if not isinstance(value, str):
        return value
    return _render_text(value, key, state).strip()


def _param(  # noqa: PLR0912
    config: dict[str, Any], key: str, kind: str, state: Any, *, op: str
) -> Any:
    """Validate (and, for strings, render) one op param. Raises ``_Fail``."""
    raw = config.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip() and kind != "content"):
        raise _Fail(f"runtime_config.{key} is required for op {op}")
    if kind == "bool":
        if not isinstance(raw, bool):
            raise _Fail(f"runtime_config.{key} must be true or false")
        return raw
    if kind == "method":
        if raw not in _MERGE_METHODS:
            raise _Fail(f"runtime_config.{key} must be one of {', '.join(_MERGE_METHODS)}")
        return raw
    if kind == "content":
        if not isinstance(raw, str):
            raise _Fail(f"runtime_config.{key} must be text")
        return _render_text(raw, key, state).strip("\n")
    value = _render(config, key, state)
    if kind == "number":
        return _number(value, key)
    if not isinstance(value, str) or not value:
        raise _Fail(f"runtime_config.{key} must be non-empty text, got {value!r}")
    if kind == "ref" and (
        not _REF_RE.match(value) or ".." in value or value.startswith(("-", "/"))
    ):
        raise _Fail(f"runtime_config.{key} {value!r} is not a valid branch name")
    if kind == "section" and not _SECTION_RE.match(value):
        raise _Fail(f"runtime_config.{key} must be letters, digits, '_', '.' or '-'")
    if kind == "sha":
        value = value.lower()
        if not _SHA_RE.match(value):
            raise _Fail(f"runtime_config.{key} must be a full 40-character commit sha")
    return value


def _render_text(value: str, key: str, state: Any) -> str:
    env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
    try:
        return env.from_string(value).render(state=state)
    except TemplateError as exc:
        raise _Fail(f"could not render runtime_config.{key} template {value!r}: {exc}") from exc


def _number(value: Any, key: str) -> int:
    if isinstance(value, bool):
        value = None
    elif isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value <= 0:
        raise _Fail(f"runtime_config.{key} must be a positive number, got {value!r}")
    return value


def _resolve_token(task: RuntimeTask, token_env: str) -> str:
    env, env_error = merge_subprocess_env(
        task.project_env_vars, task.runtime_config, instance_env_vars=task.instance_env_vars
    )
    if env_error is not None:
        raise _Fail(env_error)
    token = env.get(token_env)
    if not token:
        raise _Fail(
            f"GitHub token env var {token_env} is not set "
            "(engine env, instance env vars, project env vars or runtime_config.env)"
        )
    return token


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise _Fail(f"GitHub returned non-JSON ({response.status_code})") from exc


def _object(response: httpx.Response, path: str) -> dict[str, Any]:
    body = _json(response)
    if not isinstance(body, dict):
        raise _Fail(f"GitHub returned an unexpected body for {path}")
    return body


def _error_details(response: httpx.Response) -> str:
    """GitHub's message plus its per-field ``errors`` (422s put the reason there)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(body.get("message", ""))]
    for error in body.get("errors") or []:
        if isinstance(error, dict):
            parts.append(str(error.get("message") or error.get("code") or ""))
    return "; ".join(part for part in parts if part)


def _status_message(response: httpx.Response, where: str, repo: str) -> str:
    detail = _error_details(response)[:MESSAGE_PREVIEW]
    status = response.status_code
    if status == HTTP_UNAUTHORIZED:
        hint = "the token was rejected"
    elif status == HTTP_FORBIDDEN:
        hint = "the token lacks permission or hit a rate limit"
    elif status == HTTP_NOT_FOUND:
        hint = f"not found, or the token can't see {repo}"
    else:
        hint = "GitHub error"
    return f"GitHub returned {status} for {where} ({hint})" + (f": {detail}" if detail else "")


def _issue(body: dict[str, Any]) -> dict[str, Any]:
    labels = body.get("labels") or []
    return {
        "number": body.get("number"),
        "title": body.get("title"),
        "body": body.get("body") or "",
        "state": body.get("state"),
        "labels": [label.get("name") for label in labels if isinstance(label, dict)],
        "url": body.get("html_url"),
        "is_pull_request": "pull_request" in body,
    }


def _pr(body: dict[str, Any]) -> dict[str, Any]:
    def ref(side: str) -> dict[str, Any]:
        part = body.get(side) or {}
        return {"ref": part.get("ref"), "sha": part.get("sha")}

    return {
        "number": body.get("number"),
        "title": body.get("title"),
        "body": body.get("body") or "",
        "state": body.get("state"),
        "draft": body.get("draft"),
        "merged": body.get("merged"),
        "mergeable": body.get("mergeable"),
        "mergeable_state": body.get("mergeable_state"),
        "url": body.get("html_url"),
        "head": ref("head"),
        "base": ref("base"),
    }


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _failed(message: str, start: float) -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=_elapsed_ms(start),
        errors=[message],
        structured={"provider": "github"},
    )
