"""Generic HTTP runtime adapter.

POSTs to any JSON-speaking service: Ollama, llama.cpp servers, internal
LLM gateways, Anthropic/OpenAI-compatible proxies that don't fit the
``api-call`` (SDK) shape, custom team APIs, etc.

Usage shape:

- ``runtime_config.url`` — endpoint URL.
- ``runtime_config.method`` — ``POST`` (default), ``PUT``.
- ``runtime_config.request_template`` — JSON-shaped object (or string)
  with Jinja2 placeholders. Rendered against ``{prompt_xml,
  runtime_config}``; the result is the request body. String fields are
  templated; numbers / booleans / lists pass through as-is.
- ``runtime_config.response_extractor`` — dict mapping result keys to
  JSONPath expressions over the response body. Each match is lifted
  into ``RuntimeResult`` (e.g. ``{"output": "$.response",
  "tokens_used": "$.eval_count"}``).
- ``runtime_config.auth`` — optional. ``{"type": "bearer", "env": ...}``,
  ``{"type": "header", "name": ..., "env": ...}``, ``{"type": "basic",
  "user_env": ..., "pass_env": ...}``, or ``{"type": "none"}``. Secrets
  always come from env vars; the auth.* fields name them.
- ``runtime_config.headers`` — optional dict of static headers to send
  alongside the auth header.

The response_extractor's ``output`` mapping is the primary text. If the
match is not a string, it gets ``json.dumps``-stringified; if no match,
``output`` defaults to ``""``.

Why not just use bash + curl: bash leaks prompt templating into the
shell command (escaping nightmare with XML), headers are awkward, and
structured response parsing requires ``jq``. This adapter handles the
three cleanly with proper cancellation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Final

import httpx
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from jsonpath_ng.ext import parse as jsonpath_parse

from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.http")

DEFAULT_METHOD: Final = "POST"
ALLOWED_METHODS: Final = frozenset({"POST", "PUT"})
MS_PER_SECOND: Final = 1000
HTTP_ERROR_THRESHOLD: Final = 400
ERROR_PREVIEW_LIMIT: Final = 500


class HttpAdapter(BaseAdapter):
    """POSTs the rendered request body to ``url`` and extracts response fields via JSONPath."""

    id = "http"
    display_name = "HTTP endpoint"
    kind: RuntimeKind = "http"

    async def healthcheck(self) -> HealthStatus:
        """Adapter is always available — per-agent URL + auth are checked at execute time."""
        return HealthStatus(available=True, version=f"httpx {httpx.__version__}")

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911
        # Many returns: each guard maps to a distinct precondition failure
        # with its own error message.
        config = task.runtime_config

        validation_error = _validate_config(config)
        if validation_error is not None:
            return _failed(validation_error, duration_ms=0)

        method = str(config.get("method", DEFAULT_METHOD)).upper()
        url = config["url"]

        # Render request body via sandboxed Jinja2 over the template tree.
        try:
            body = _render_template(
                config["request_template"],
                _build_template_env(),
                {
                    "prompt_xml": task.prompt_xml,
                    "runtime_config": config,
                },
            )
        except TemplateError as exc:
            return _failed(
                f"Failed to render request_template: {exc}",
                duration_ms=0,
                url=url,
            )

        # Build auth + extra headers.
        try:
            headers = _build_headers(config.get("auth"), config.get("headers"))
        except ValueError as exc:
            return _failed(str(exc), duration_ms=0, url=url)

        timeout_seconds = max(task.timeout_ms, 1) / MS_PER_SECOND

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=body if isinstance(body, (dict, list)) else None,
                    content=body if isinstance(body, str) else None,
                    headers=headers,
                )
            except httpx.TimeoutException:
                return _failed(
                    f"HTTP request timed out after {task.timeout_ms}ms",
                    duration_ms=_elapsed_ms(start),
                    url=url,
                    timed_out=True,
                )
            except httpx.RequestError as exc:
                return _failed(
                    f"HTTP request failed: {exc}",
                    duration_ms=_elapsed_ms(start),
                    url=url,
                )

        duration_ms = _elapsed_ms(start)

        if response.status_code >= HTTP_ERROR_THRESHOLD:
            preview = response.text[:ERROR_PREVIEW_LIMIT]
            return _failed(
                f"HTTP {response.status_code}: {preview}",
                duration_ms=duration_ms,
                url=url,
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError:
            return _failed(
                "Response was not valid JSON. Use a different runtime if the "
                "service returns plain text.",
                duration_ms=duration_ms,
                url=url,
                status_code=response.status_code,
            )

        # Extract fields via JSONPath.
        extractors = config["response_extractor"]
        extracted = _apply_extractors(extractors, payload)
        output_text = _coerce_to_string(extracted.get("output", ""))
        tokens_used = _coerce_to_int(extracted.get("tokens_used"))
        cost_usd = _coerce_to_float(extracted.get("cost_usd"))

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            errors=[],
            structured={
                "provider": "http",
                "url": url,
                "method": method,
                "status_code": response.status_code,
                # Only the named extractions are persisted (saved into
                # NodeExecutionLog.output_json). The full response payload
                # is intentionally NOT stored to avoid bloating the DB and
                # creating an unintended data-retention surface for large
                # or sensitive responses. To capture the raw response,
                # add a JSONPath like `"_raw": "$"` to response_extractor.
                "extracted": {k: v for k, v in extracted.items() if k != "output"},
                "timed_out": False,
            },
        )


def _validate_config(config: dict[str, Any]) -> str | None:  # noqa: PLR0911,PLR0912
    """Per-call validation. Returns an error message or None when OK."""
    url = config.get("url")
    if not isinstance(url, str) or not url:
        return "runtime_config.url is required"
    if not (url.startswith("http://") or url.startswith("https://")):
        return "runtime_config.url must start with http:// or https://"

    method = config.get("method", DEFAULT_METHOD)
    if not isinstance(method, str) or method.upper() not in ALLOWED_METHODS:
        return f"runtime_config.method must be one of {sorted(ALLOWED_METHODS)}"

    template = config.get("request_template")
    if not isinstance(template, (dict, list, str)):
        return (
            "runtime_config.request_template is required "
            "(dict / list / string with Jinja2 placeholders)"
        )

    extractor = config.get("response_extractor")
    if not isinstance(extractor, dict):
        return (
            "runtime_config.response_extractor is required "
            '(dict mapping field names → JSONPath, e.g. {"output": "$.response"})'
        )
    for key, expr in extractor.items():
        if not isinstance(key, str) or not isinstance(expr, str):
            return (
                "runtime_config.response_extractor entries must be "
                "{string: string}; one of them isn't"
            )
        try:
            jsonpath_parse(expr)
        except Exception as exc:  # jsonpath-ng raises various exception types
            return f"Invalid JSONPath '{expr}' for '{key}': {exc}"

    if "output" not in extractor:
        return (
            "runtime_config.response_extractor must include an 'output' key "
            "(maps to RuntimeResult.output)"
        )

    headers = config.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            return "runtime_config.headers must be a dict"
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return "runtime_config.headers entries must be {string: string}; one of them isn't"

    auth = config.get("auth")
    if auth is not None:
        return _validate_auth(auth)

    return None


def _validate_auth(auth: Any) -> str | None:  # noqa: PLR0911
    """Return an error message for a malformed auth config, or None.

    Many returns: each guard maps to a distinct precondition failure.
    """
    if not isinstance(auth, dict):
        return "runtime_config.auth must be a dict"
    auth_type = auth.get("type")
    if auth_type not in {"none", "bearer", "header", "basic"}:
        return "runtime_config.auth.type must be one of: 'none', 'bearer', 'header', 'basic'"
    if auth_type == "bearer":
        if not isinstance(auth.get("env"), str) or not auth.get("env"):
            return "runtime_config.auth.env is required for type='bearer'"
    elif auth_type == "header":
        if not isinstance(auth.get("name"), str) or not auth.get("name"):
            return "runtime_config.auth.name is required for type='header'"
        if not isinstance(auth.get("env"), str) or not auth.get("env"):
            return "runtime_config.auth.env is required for type='header'"
    elif auth_type == "basic":
        for field in ("user_env", "pass_env"):
            value = auth.get(field)
            if not isinstance(value, str) or not value:
                return f"runtime_config.auth.{field} is required for type='basic'"
    return None


def _build_template_env() -> SandboxedEnvironment:
    """Sandboxed Jinja2 — same defence-in-depth as prompt-dsl.

    ``StrictUndefined`` mirrors prompt-dsl: a typo or missing variable
    fails fast with a descriptive ``UndefinedError`` instead of silently
    rendering as an empty string and shipping a malformed request.
    """
    return SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def _render_template(template: Any, env: SandboxedEnvironment, ctx: dict[str, Any]) -> Any:
    """Recursively render strings in a JSON-shaped template; pass other types through."""
    if isinstance(template, str):
        return env.from_string(template).render(**ctx)
    if isinstance(template, list):
        return [_render_template(item, env, ctx) for item in template]
    if isinstance(template, dict):
        return {k: _render_template(v, env, ctx) for k, v in template.items()}
    return template


def _build_headers(
    auth: Any,
    extra_headers: Any,
) -> dict[str, str]:
    """Resolve auth header from env vars, merge with any static extras.

    Raises ``ValueError`` when an env var named in ``auth`` isn't set, or
    when ``extra_headers`` contains a non-string header name or value.
    Validation has already accepted dict[str, str] at config time, but a
    second guard here surfaces post-validation drift (e.g. someone hand-
    edited the row in the DB) instead of silently dropping headers.
    """
    headers: dict[str, str] = {}
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(
                    "runtime_config.headers must contain only string names "
                    f"and values; got {type(key).__name__}={key!r}, "
                    f"{type(value).__name__}={value!r}"
                )
            headers[key] = value

    if not isinstance(auth, dict):
        return headers

    auth_type = auth.get("type")
    if auth_type in (None, "none"):
        return headers
    if auth_type == "bearer":
        token = os.environ.get(auth["env"])
        if not token:
            raise ValueError(
                f"Auth env var {auth['env']} not set (referenced by runtime_config.auth)"
            )
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "header":
        value = os.environ.get(auth["env"])
        if not value:
            raise ValueError(
                f"Auth env var {auth['env']} not set (referenced by runtime_config.auth)"
            )
        headers[auth["name"]] = value
    elif auth_type == "basic":
        user = os.environ.get(auth["user_env"])
        password = os.environ.get(auth["pass_env"])
        if not user or not password:
            missing = auth["user_env"] if not user else auth["pass_env"]
            raise ValueError(f"Auth env var {missing} not set (referenced by runtime_config.auth)")
        creds = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {creds}"
    return headers


def _apply_extractors(
    extractors: dict[str, str],
    payload: Any,
) -> dict[str, Any]:
    """Run each JSONPath against the payload, collect the first match per key.

    Iterates lazily — broad JSONPaths over large payloads stop after the
    first match instead of materialising the full result list.
    """
    out: dict[str, Any] = {}
    for key, expr_str in extractors.items():
        match_value: Any = None
        try:
            expr = jsonpath_parse(expr_str)
            for match in expr.find(payload):
                match_value = match.value
                break
        except Exception:
            logger.exception("JSONPath '%s' failed against payload", expr_str)
        out[key] = match_value
    return out


def _coerce_to_string(value: Any) -> str:
    """Best-effort string coercion — empty for missing, JSON for non-strings."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _coerce_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _failed(
    message: str,
    *,
    duration_ms: int,
    url: str | None = None,
    status_code: int | None = None,
    timed_out: bool = False,
) -> RuntimeResult:
    structured: dict[str, Any] = {
        "provider": "http",
        "timed_out": timed_out,
    }
    if url is not None:
        structured["url"] = url
    if status_code is not None:
        structured["status_code"] = status_code
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=duration_ms,
        errors=[message],
        structured=structured,
    )
