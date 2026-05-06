"""Tests for the http runtime adapter — httpx is mocked."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from dap_runtimes import HttpAdapter
from dap_types import RuntimeTask

_PATCH_PATH = "dap_runtimes.adapters.http.httpx.AsyncClient"

# Long enough (>= _MIN_SECRET_LEN) that the redaction helper actually
# scrubs it; "test-token" works too but a clearly-fake API-key shape
# makes the redaction tests' intent obvious.
_BEARER_TOKEN = "sk-secret-token-123-do-not-leak"


@pytest.fixture
def with_ollama_key() -> Iterator[None]:
    saved = os.environ.get("OLLAMA_API_KEY")
    os.environ["OLLAMA_API_KEY"] = _BEARER_TOKEN
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("OLLAMA_API_KEY", None)
        else:
            os.environ["OLLAMA_API_KEY"] = saved


def _task(
    *,
    url: str = "http://localhost:11434/api/generate",
    method: str = "POST",
    request_template: Any = None,
    response_extractor: Any = None,
    auth: Any = None,
    headers: Any = None,
    timeout_ms: int = 60_000,
) -> RuntimeTask:
    if request_template is None:
        request_template = {
            "model": "{{ runtime_config.model_id }}",
            "prompt": "{{ prompt_xml }}",
            "stream": False,
        }
    if response_extractor is None:
        response_extractor = {"output": "$.response", "tokens_used": "$.eval_count"}
    runtime_config: dict[str, Any] = {
        "url": url,
        "method": method,
        "request_template": request_template,
        "response_extractor": response_extractor,
        "model_id": "llama3.2",
    }
    if auth is not None:
        runtime_config["auth"] = auth
    if headers is not None:
        runtime_config["headers"] = headers
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>r</role></agent_prompt>",
        working_directory="/tmp",
        timeout_ms=timeout_ms,
        runtime_config=runtime_config,
    )


def _mock_response(
    *,
    status_code: int = 200,
    json_payload: Any = None,
    text: str = "",
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text or (str(json_payload) if json_payload is not None else "")
    if json_payload is not None:
        response.json = MagicMock(return_value=json_payload)
    else:
        response.json = MagicMock(side_effect=ValueError("not json"))
    return response


def _mock_client(
    response: MagicMock | None = None,
    *,
    request_side_effect: Exception | None = None,
) -> MagicMock:
    """Build an httpx.AsyncClient mock with async-context-manager behaviour."""
    client = MagicMock()
    if request_side_effect is not None:
        client.request = AsyncMock(side_effect=request_side_effect)
    else:
        client.request = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_always_available() -> None:
    """URL + auth are per-agent, so adapter healthcheck just reports presence."""
    adapter = HttpAdapter()
    health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None
    assert "httpx" in health.version


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_missing_url_returns_error() -> None:
    adapter = HttpAdapter()
    task = _task(url="")
    result = await adapter.execute(task)
    assert result.success is False
    assert any("url is required" in e for e in result.errors)


async def test_invalid_url_scheme_returns_error() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(url="ftp://example.com"))
    assert result.success is False
    assert any("http://" in e for e in result.errors)


async def test_invalid_method_returns_error() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(method="GET"))
    assert result.success is False
    assert any("method" in e for e in result.errors)


async def test_missing_request_template_returns_error() -> None:
    adapter = HttpAdapter()
    task = _task()
    task.runtime_config.pop("request_template")
    result = await adapter.execute(task)
    assert result.success is False
    assert any("request_template" in e for e in result.errors)


async def test_missing_response_extractor_returns_error() -> None:
    adapter = HttpAdapter()
    task = _task()
    task.runtime_config.pop("response_extractor")
    result = await adapter.execute(task)
    assert result.success is False
    assert any("response_extractor" in e for e in result.errors)


async def test_response_extractor_must_have_output_key() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(response_extractor={"tokens": "$.count"}))
    assert result.success is False
    assert any("'output'" in e for e in result.errors)


async def test_invalid_jsonpath_in_extractor_returns_error() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(response_extractor={"output": "$..[bad jsonpath"}))
    assert result.success is False
    assert any("Invalid JSONPath" in e for e in result.errors)


async def test_invalid_auth_type_returns_error() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(auth={"type": "oauth"}))
    assert result.success is False
    assert any("auth.type" in e for e in result.errors)


async def test_bearer_auth_missing_env_field_returns_error() -> None:
    adapter = HttpAdapter()
    result = await adapter.execute(_task(auth={"type": "bearer"}))
    assert result.success is False
    assert any("auth.env" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Successful execute (mocked httpx)
# ---------------------------------------------------------------------------


async def test_execute_success_extracts_output_and_tokens() -> None:
    adapter = HttpAdapter()
    response = _mock_response(
        status_code=200,
        json_payload={"response": "hi from local model", "eval_count": 42},
    )
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client) as cls_mock:
        result = await adapter.execute(_task())

        # AsyncClient(...) was called with timeout
        ctor_kwargs = cls_mock.call_args.kwargs
        assert "timeout" in ctor_kwargs

    assert result.success is True
    assert result.output == "hi from local model"
    assert result.tokens_used == 42
    assert result.structured is not None
    assert result.structured["status_code"] == 200
    assert result.structured["url"] == "http://localhost:11434/api/generate"


async def test_request_template_rendered_with_prompt_and_runtime_config() -> None:
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"response": "ok"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        await adapter.execute(_task())

    call_kwargs = client.request.call_args.kwargs
    body = call_kwargs["json"]
    assert body["model"] == "llama3.2"
    assert body["prompt"].startswith("<agent_prompt>")
    assert body["stream"] is False


async def test_string_template_passes_through_as_content() -> None:
    """String request_template is sent as raw body, not as JSON."""
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"response": "ok"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        await adapter.execute(_task(request_template="raw body with {{ prompt_xml }}"))

    call_kwargs = client.request.call_args.kwargs
    assert call_kwargs["content"].startswith("raw body with <agent_prompt>")
    assert call_kwargs["json"] is None


async def test_bearer_auth_attaches_token(with_ollama_key: None) -> None:
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"response": "ok"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        await adapter.execute(_task(auth={"type": "bearer", "env": "OLLAMA_API_KEY"}))

    headers = client.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {_BEARER_TOKEN}"


async def test_basic_auth_encodes_credentials() -> None:
    saved_user = os.environ.get("HTTP_USER")
    saved_pass = os.environ.get("HTTP_PASS")
    os.environ["HTTP_USER"] = "alice"
    os.environ["HTTP_PASS"] = "s3cret"
    try:
        adapter = HttpAdapter()
        response = _mock_response(json_payload={"response": "ok"})
        client = _mock_client(response)

        with patch(_PATCH_PATH, return_value=client):
            await adapter.execute(
                _task(
                    auth={
                        "type": "basic",
                        "user_env": "HTTP_USER",
                        "pass_env": "HTTP_PASS",
                    }
                )
            )

        headers = client.request.call_args.kwargs["headers"]
        # base64("alice:s3cret") = "YWxpY2U6czNjcmV0"
        assert headers["Authorization"] == "Basic YWxpY2U6czNjcmV0"
    finally:
        if saved_user is None:
            os.environ.pop("HTTP_USER", None)
        else:
            os.environ["HTTP_USER"] = saved_user
        if saved_pass is None:
            os.environ.pop("HTTP_PASS", None)
        else:
            os.environ["HTTP_PASS"] = saved_pass


async def test_custom_header_auth_uses_named_env() -> None:
    saved = os.environ.get("CUSTOM_KEY")
    os.environ["CUSTOM_KEY"] = "k-12345"
    try:
        adapter = HttpAdapter()
        response = _mock_response(json_payload={"response": "ok"})
        client = _mock_client(response)

        with patch(_PATCH_PATH, return_value=client):
            await adapter.execute(
                _task(auth={"type": "header", "name": "X-API-Key", "env": "CUSTOM_KEY"})
            )

        headers = client.request.call_args.kwargs["headers"]
        assert headers["X-API-Key"] == "k-12345"
        assert "Authorization" not in headers  # not bearer
    finally:
        if saved is None:
            os.environ.pop("CUSTOM_KEY", None)
        else:
            os.environ["CUSTOM_KEY"] = saved


async def test_static_extra_headers_merged() -> None:
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"response": "ok"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        await adapter.execute(_task(headers={"X-Trace-ID": "abc-123", "X-Project": "dap"}))

    headers = client.request.call_args.kwargs["headers"]
    assert headers["X-Trace-ID"] == "abc-123"
    assert headers["X-Project"] == "dap"


async def test_jsonpath_with_nested_response() -> None:
    """JSONPath should reach into nested fields."""
    adapter = HttpAdapter()
    response = _mock_response(
        json_payload={
            "choices": [{"message": {"content": "nested response"}}],
            "usage": {"total_tokens": 256},
        }
    )
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(
                response_extractor={
                    "output": "$.choices[0].message.content",
                    "tokens_used": "$.usage.total_tokens",
                }
            )
        )

    assert result.success is True
    assert result.output == "nested response"
    assert result.tokens_used == 256


async def test_non_string_output_match_is_json_stringified() -> None:
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"data": [1, 2, 3]})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task(response_extractor={"output": "$.data"}))

    assert result.success is True
    assert result.output == "[1, 2, 3]"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_missing_bearer_env_returns_error() -> None:
    saved = os.environ.pop("MISSING_KEY", None)
    try:
        adapter = HttpAdapter()
        result = await adapter.execute(_task(auth={"type": "bearer", "env": "MISSING_KEY"}))
        assert result.success is False
        assert any("MISSING_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["MISSING_KEY"] = saved


async def test_http_4xx_returns_failure_with_preview() -> None:
    adapter = HttpAdapter()
    response = _mock_response(status_code=401, text="Unauthorized: bad token")
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("401" in e and "Unauthorized" in e for e in result.errors)


async def test_http_4xx_redacts_bearer_token_echoed_in_error_body(
    with_ollama_key: None,
) -> None:
    """Some upstreams echo the Authorization header in their error body
    (auth gateways, debug servers, 401 traces). The error preview must
    redact the secret before storing it in errors[]/dashboard/logs (#212).
    """
    # The fixture sets OLLAMA_API_KEY to _BEARER_TOKEN (defined at module top).
    # Upstream echoes the full Authorization header in its error body.
    adapter = HttpAdapter()
    response = _mock_response(
        status_code=401,
        text=(
            "Unauthorized. Got header: "
            f"Authorization: Bearer {_BEARER_TOKEN}. Please retry."
        ),
    )
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(auth={"type": "bearer", "env": "OLLAMA_API_KEY"})
        )

    assert result.success is False
    [error] = result.errors
    # The token must NOT appear anywhere in the surfaced error.
    assert _BEARER_TOKEN not in error
    # The redaction marker SHOULD appear so callers see the body got scrubbed.
    assert "[REDACTED]" in error
    # And we still preserve enough context to be useful: status code,
    # surrounding text from the upstream body.
    assert "401" in error
    assert "Unauthorized" in error


async def test_http_4xx_redacts_bare_token_without_bearer_prefix(
    with_ollama_key: None,
) -> None:
    """Upstream may echo just the raw token without the 'Bearer ' prefix.
    We strip known prefixes and redact the bare secret too (#212).
    """
    adapter = HttpAdapter()
    response = _mock_response(
        status_code=403,
        text=f"Forbidden. Token {_BEARER_TOKEN} is not authorized.",
    )
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(auth={"type": "bearer", "env": "OLLAMA_API_KEY"})
        )

    [error] = result.errors
    assert _BEARER_TOKEN not in error
    assert "[REDACTED]" in error


async def test_http_4xx_redacts_custom_header_auth(with_ollama_key: None) -> None:
    """auth.type='header' values (X-API-Key etc.) must also be redacted (#212)."""
    adapter = HttpAdapter()
    response = _mock_response(
        status_code=401,
        text=f"Bad credentials: X-API-Key={_BEARER_TOKEN} not recognized",
    )
    client = _mock_client(response)
    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(auth={"type": "header", "name": "X-API-Key", "env": "OLLAMA_API_KEY"})
        )

    [error] = result.errors
    assert _BEARER_TOKEN not in error
    assert "[REDACTED]" in error


async def test_http_4xx_redacts_user_supplied_cookie() -> None:
    """User-supplied Cookie / X-API-Key in runtime_config.headers is also scrubbed (#212).

    Headers whose name matches a sensitive pattern (Cookie / Authorization /
    *Token* / *API-Key* / *Auth* / *Secret*) are flagged for redaction even
    when they come from runtime_config.headers rather than from auth config.
    """
    adapter = HttpAdapter()
    cookie = "session=secret-cookie-value-12345"
    response = _mock_response(
        status_code=403,
        text=f"Forbidden. Got Cookie: {cookie} (unauthorized)",
    )
    client = _mock_client(response)
    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task(headers={"Cookie": cookie}))

    [error] = result.errors
    assert "secret-cookie-value-12345" not in error
    assert "[REDACTED]" in error


async def test_http_4xx_does_not_redact_benign_headers() -> None:
    """User-supplied benign headers (Content-Type, User-Agent) must NOT be
    redacted from the preview — that would mangle useful diagnostic context (#212).
    """
    adapter = HttpAdapter()
    # Upstream's error body legitimately mentions the Content-Type — must not
    # be redacted just because we sent that header.
    response = _mock_response(
        status_code=415,
        text="Unsupported Media Type: expected application/json got something-else",
    )
    client = _mock_client(response)
    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "dap-engine/test",
                    "X-Trace-Id": "trace-12345",
                }
            )
        )

    [error] = result.errors
    # None of the benign headers should be redacted.
    assert "[REDACTED]" not in error
    assert "application/json" in error
    assert "415" in error


async def test_non_json_response_returns_error() -> None:
    adapter = HttpAdapter()
    response = _mock_response(status_code=200, text="just plain text")
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("not valid JSON" in e for e in result.errors)


async def test_timeout_marked_and_returns_failure() -> None:
    adapter = HttpAdapter()
    client = _mock_client(request_side_effect=httpx.TimeoutException("timeout"))

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task(timeout_ms=200))

    assert result.success is False
    assert any("timed out" in e.lower() for e in result.errors)
    assert result.structured is not None
    assert result.structured["timed_out"] is True


async def test_connection_error_returns_failure() -> None:
    adapter = HttpAdapter()
    client = _mock_client(request_side_effect=httpx.ConnectError("connection refused"))

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("HTTP request failed" in e for e in result.errors)


async def test_headers_must_be_dict_of_strings() -> None:
    """Validation rejects non-string entries at config time, not silently."""
    adapter = HttpAdapter()
    result = await adapter.execute(_task(headers={"X-Trace-ID": 123}))
    assert result.success is False
    assert any("headers entries" in e for e in result.errors)


async def test_template_strict_undefined_fails_on_missing_var() -> None:
    """StrictUndefined: a typo'd template variable fails fast, not as ''."""
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"response": "ok"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(
                request_template={
                    "model": "{{ runtime_config.does_not_exist }}",
                    "prompt": "{{ prompt_xml }}",
                },
            )
        )

    assert result.success is False
    assert any("render request_template" in e.lower() for e in result.errors)


async def test_full_payload_not_persisted_in_structured() -> None:
    """The full response is intentionally not stored — only `extracted` is."""
    adapter = HttpAdapter()
    response = _mock_response(
        json_payload={"response": "ok", "secret": "should-not-be-saved", "huge": "x" * 100_000}
    )
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.structured is not None
    assert "payload" not in result.structured
    # The named extractions land in `extracted` so the user keeps what
    # they cared about; the unrequested fields are dropped.
    assert "extracted" in result.structured


async def test_user_can_opt_into_full_raw_response_via_extractor() -> None:
    """For debugging, an explicit JSONPath `$` in the extractor pulls the raw."""
    adapter = HttpAdapter()
    payload = {"response": "ok", "metadata": {"trace": "abc"}}
    response = _mock_response(json_payload=payload)
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(
            _task(
                response_extractor={"output": "$.response", "_raw": "$"},
            )
        )

    assert result.success is True
    assert result.structured is not None
    assert result.structured["extracted"]["_raw"] == payload


async def test_jsonpath_no_match_yields_empty_output() -> None:
    """No-match extractor returns None → output coerced to empty string."""
    adapter = HttpAdapter()
    response = _mock_response(json_payload={"unrelated": "data"})
    client = _mock_client(response)

    with patch(_PATCH_PATH, return_value=client):
        result = await adapter.execute(_task(response_extractor={"output": "$.missing_field"}))

    assert result.success is True
    assert result.output == ""
