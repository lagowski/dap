"""The ``github`` runtime: declarative GitHub operations (#920, part of #810).

Read operations (this module's first slice):

- ``op: read_issue``: ``repo`` + ``issue`` → title, body, state, label names, URL and
  whether the number is actually a PR.
- ``op: read_pr``: ``repo`` + ``pr`` → title, body, state, draft/merged, mergeability,
  head/base ref and sha, URL, and every changed file name (paginated).

Config:

- ``repo`` / ``issue`` / ``pr`` may be literals or Jinja templates rendered against
  ``{"state": <pipeline state>}``. The engine injects that state into every task as
  ``runtime_config["__pipeline_state"]``. Sandboxed, with ``StrictUndefined`` (as in
  the ``http`` runtime), so a typo fails the node instead of calling GitHub with an
  empty value.
- ``token_env``: name of the env var holding the token (default ``GH_TOKEN``), resolved
  through DAP's env layering: engine env < instance env vars < project env vars <
  ``runtime_config.env``. Role-separated tokens (``CORTEX_GH_TOKEN_READ`` …) plug in by
  name. The token is only ever sent as an ``Authorization`` header and is scrubbed
  from every message.
- ``state_key``: where the result lands in state (default ``github_issue`` /
  ``github_pr``). It is returned as ``structured["state_delta"]``, which the engine
  merges like any other runtime's (unknown keys go to ``extensions``).
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
_REPO_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# op -> (number param, default state key)
_OPS: Final[dict[str, tuple[str, str]]] = {
    "read_issue": ("issue", "github_issue"),
    "read_pr": ("pr", "github_pr"),
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

        logger.info("github %s %s#%s", call.op, call.repo, call.number)
        return RuntimeResult(
            success=True,
            output=json.dumps(data, ensure_ascii=False),
            duration_ms=_elapsed_ms(start),
            structured={
                "state_delta": {call.state_key: data},
                "github": {"op": call.op, "repo": call.repo, "number": call.number},
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
            api = _Api(client, call)
            if call.op == "read_issue":
                return _issue(await api.get(f"issues/{call.number}"))
            pr = await api.get(f"pulls/{call.number}")
            return {**_pr(pr), "files": await api.files()}


class _Call:
    """A validated, rendered operation."""

    def __init__(
        self, *, op: str, repo: str, number: int, state_key: str, token_env: str, api_url: str
    ) -> None:
        self.op = op
        self.repo = repo
        self.number = number
        self.state_key = state_key
        self.token_env = token_env
        self.api_url = api_url

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> _Call:
        op = config.get("op")
        if not isinstance(op, str) or not op:
            raise _Fail(f"runtime_config.op is required (one of: {', '.join(_OPS)})")
        if op not in _OPS:
            raise _Fail(f"runtime_config.op {op!r} is not supported (one of: {', '.join(_OPS)})")
        number_key, default_state_key = _OPS[op]

        state = config.get("__pipeline_state") or {}
        repo = _render(config, "repo", state)
        # "." / ".." segments would let the request path climb out of /repos/<owner>/<name>.
        if (
            not isinstance(repo, str)
            or not _REPO_RE.match(repo)
            or any(part in {".", ".."} for part in repo.split("/"))
        ):
            raise _Fail(f"runtime_config.repo must be owner/name, got {repo!r}")
        number = _number(_render(config, number_key, state), number_key)

        state_key = config.get("state_key", default_state_key)
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
            number=number,
            state_key=state_key.strip(),
            token_env=token_env,
            api_url=api_url.rstrip("/"),
        )


class _Api:
    """GET helpers bound to one repo, with GitHub's errors mapped to messages."""

    def __init__(self, client: httpx.AsyncClient, call: _Call) -> None:
        self._client = client
        self._call = call
        self._base = f"{call.api_url}/repos/{call.repo}"

    async def get(self, path: str) -> dict[str, Any]:
        response = await self._request(f"{self._base}/{path}")
        body = _json(response)
        if not isinstance(body, dict):
            raise _Fail(f"GitHub returned an unexpected body for {self._call.repo}/{path}")
        return body

    async def files(self) -> list[str]:
        url: str | None = f"{self._base}/pulls/{self._call.number}/files"
        params: dict[str, int] | None = {"per_page": PER_PAGE}
        names: list[str] = []
        for _ in range(MAX_FILE_PAGES):
            if url is None:
                break
            response = await self._request(url, params)
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

    async def _request(self, url: str, params: dict[str, int] | None = None) -> httpx.Response:
        where = url.removeprefix(self._call.api_url + "/")
        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise _Fail(f"GitHub request timed out for {where}") from exc
        except httpx.RequestError as exc:
            raise _Fail(f"GitHub request failed for {where}: {exc}") from exc
        if response.status_code >= HTTP_ERROR:
            raise _Fail(_status_message(response, where, self._call.repo))
        return response


def _render(config: dict[str, Any], key: str, state: Any) -> Any:
    value = config.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _Fail(f"runtime_config.{key} is required")
    if not isinstance(value, str):
        return value
    env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
    try:
        return env.from_string(value).render(state=state).strip()
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


def _status_message(response: httpx.Response, where: str, repo: str) -> str:
    try:
        detail = str(response.json().get("message", ""))[:MESSAGE_PREVIEW]
    except (ValueError, AttributeError):
        detail = ""
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
