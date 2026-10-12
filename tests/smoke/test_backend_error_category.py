"""Backend error categories on failed results (#934, part of #811).

A failed node carries ``structured["error_category"]`` when the failure came
from the backend — the provider, the network, the binary — rather than from
the node's own config or code. ``is_backend_error`` is what #935's fallback
chain decides on: switching model can fix a backend error, and nothing else.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import httpx2
import openai
import pytest
from dap_engine.diagnostics.error_explainer import explain_node_error
from dap_runtimes import ApiCallAdapter, ClaudeCodeAdapter, GithubAdapter, HttpAdapter
from dap_types import (
    BACKEND_ERROR_CATEGORIES,
    RuntimeResult,
    RuntimeTask,
    error_category,
    is_backend_error,
)
from google.genai import errors as genai_errors

from .conftest import build_subprocess_mock

# --------------------------------------------------------------------------- is_backend_error


def _result(*, success: bool = False, structured: dict[str, Any] | None = None) -> RuntimeResult:
    return RuntimeResult(success=success, output="", errors=[] if success else ["x"], structured=structured)


def test_the_category_set_is_closed() -> None:
    assert frozenset({"rate_limit", "out_of_credits", "timeout", "unavailable"}) == (
        BACKEND_ERROR_CATEGORIES
    )


@pytest.mark.parametrize("category", sorted(BACKEND_ERROR_CATEGORIES))
def test_each_category_is_a_backend_error(category: str) -> None:
    result = _result(structured={"error_category": category})
    assert error_category(result) == category
    assert is_backend_error(result)


@pytest.mark.parametrize(
    "structured",
    [None, {}, {"error_category": None}, {"error_category": "config"}, {"error_category": 3}],
)
def test_anything_else_is_not_a_backend_error(structured: dict[str, Any] | None) -> None:
    result = _result(structured=structured)
    assert error_category(result) is None
    assert not is_backend_error(result)


def test_a_successful_result_is_never_a_backend_error() -> None:
    result = _result(success=True, structured={"error_category": "timeout"})
    assert error_category(result) is None
    assert not is_backend_error(result)


# --------------------------------------------------------------------------- api-call

_ANTHROPIC = "dap_runtimes.adapters._providers._anthropic.AsyncAnthropic"
_OPENAI = "dap_runtimes.adapters._providers._openai.AsyncOpenAI"
_GEMINI = "dap_runtimes.adapters._providers._gemini.genai.Client"


@pytest.fixture
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "sk-test-fake")


def _api_task(provider: str, model_id: str) -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-934",
        prompt_xml="<agent_prompt><role>x</role></agent_prompt>",
        working_directory="/tmp",
        runtime_config={"provider": provider, "model_id": model_id, "max_tokens": 1024},
    )


def _anthropic_response(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx2.Response(status, request=request)


def _anthropic_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


async def _run_anthropic(error: Exception) -> RuntimeResult:
    with patch(_ANTHROPIC) as mock_cls:
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=error)
        client.close = AsyncMock()
        mock_cls.return_value = client
        result = await ApiCallAdapter().execute(_api_task("anthropic", "claude-sonnet-4-6"))
    assert result.success is False
    return result


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            anthropic.RateLimitError(
                "Rate limit exceeded", response=_anthropic_response(429), body=None
            ),
            "rate_limit",
        ),
        (
            anthropic.BadRequestError(
                "Your credit balance is too low to access the Anthropic API",
                response=_anthropic_response(400),
                body=None,
            ),
            "out_of_credits",
        ),
        (anthropic.APITimeoutError(request=_anthropic_request()), "timeout"),
        (anthropic.APIConnectionError(request=_anthropic_request()), "unavailable"),
        (
            anthropic.InternalServerError("overloaded", response=_anthropic_response(529), body=None),
            "unavailable",
        ),
        (
            anthropic.InternalServerError("boom", response=_anthropic_response(500), body=None),
            "unavailable",
        ),
        # Not a backend error: switching model can't fix these.
        (
            anthropic.AuthenticationError("bad key", response=_anthropic_response(401), body=None),
            None,
        ),
        (
            anthropic.BadRequestError("max_tokens too big", response=_anthropic_response(400), body=None),
            None,
        ),
        (anthropic.NotFoundError("no model", response=_anthropic_response(404), body=None), None),
        (ValueError("simulated SDK bug"), None),
    ],
)
async def test_anthropic_failures_carry_their_category(
    provider_keys: None, error: Exception, expected: str | None
) -> None:
    result = await _run_anthropic(error)
    assert error_category(result) == expected
    assert is_backend_error(result) is (expected is not None)


async def test_api_call_keeps_its_error_message(provider_keys: None) -> None:
    result = await _run_anthropic(
        anthropic.RateLimitError("Rate limit exceeded", response=_anthropic_response(429), body=None)
    )
    assert result.errors == ["Rate limited: Rate limit exceeded"]


async def test_api_call_config_errors_have_no_category(provider_keys: None) -> None:
    result = await ApiCallAdapter().execute(_api_task("no-such-provider", "m"))
    assert result.success is False
    assert error_category(result) is None


def _openai_response(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx2.Response(status, request=request)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.RateLimitError(
                "Error code: 429 - insufficient_quota", response=_openai_response(429), body=None
            ),
            "out_of_credits",
        ),
        (
            openai.RateLimitError("Rate limit reached", response=_openai_response(429), body=None),
            "rate_limit",
        ),
        (openai.APITimeoutError(request=_openai_response(200).request), "timeout"),
        (openai.APIConnectionError(request=_openai_response(200).request), "unavailable"),
        (
            openai.InternalServerError("bad gateway", response=_openai_response(502), body=None),
            "unavailable",
        ),
        (openai.BadRequestError("bad request", response=_openai_response(400), body=None), None),
    ],
)
async def test_openai_failures_carry_their_category(
    provider_keys: None, error: Exception, expected: str | None
) -> None:
    with patch(_OPENAI) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=error)
        client.close = AsyncMock()
        mock_cls.return_value = client
        result = await ApiCallAdapter().execute(_api_task("openai", "gpt-5-mini"))
    assert result.success is False
    assert error_category(result) == expected


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (429, "429 RESOURCE_EXHAUSTED", "rate_limit"),
        (503, "503 UNAVAILABLE: the model is overloaded", "unavailable"),
        (400, "400 INVALID_ARGUMENT", None),
    ],
)
async def test_gemini_failures_carry_their_category(
    provider_keys: None, code: int, message: str, expected: str | None
) -> None:
    error = genai_errors.APIError(code, {"error": {"code": code, "message": message}})
    with patch(_GEMINI) as mock_cls:
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=error)
        mock_cls.return_value = client
        result = await ApiCallAdapter().execute(_api_task("gemini", "gemini-3.0-flash"))
    assert result.success is False
    assert error_category(result) == expected


# --------------------------------------------------------------------------- CLI base

_SUBPROCESS = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"
_WHICH = "dap_runtimes.adapters._cli_base.shutil.which"


@pytest.fixture
def anthropic_key() -> Iterator[None]:
    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fake"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved


def _cli_task(**config: Any) -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-934",
        prompt_xml="<agent_prompt><role>impl</role></agent_prompt>",
        working_directory="/tmp",
        timeout_ms=200,
        runtime_config={"model_id": "claude-opus-4-7", **config},
    )


async def _run_cli(proc: MagicMock | None = None, *, spawn_error: Exception | None = None) -> RuntimeResult:
    spawn = AsyncMock(side_effect=spawn_error) if spawn_error else AsyncMock(return_value=proc)
    with patch(_WHICH, return_value="/usr/local/bin/claude"), patch(_SUBPROCESS, spawn):
        result = await ClaudeCodeAdapter().execute(_cli_task())
    assert result.success is False
    return result


async def test_cli_timeout_is_a_timeout(anthropic_key: None) -> None:
    proc = build_subprocess_mock(side_effect=TimeoutError())
    proc.returncode = None
    result = await _run_cli(proc)
    assert error_category(result) == "timeout"
    assert result.structured is not None
    assert result.structured["timed_out"] is True


async def test_cli_binary_missing_from_path_is_unavailable(anthropic_key: None) -> None:
    with patch(_WHICH, return_value=None):
        result = await ClaudeCodeAdapter().execute(_cli_task())
    assert result.success is False
    assert error_category(result) == "unavailable"
    assert any("Claude binary not found" in e for e in result.errors)


async def test_cli_binary_vanishing_at_spawn_is_unavailable(anthropic_key: None) -> None:
    result = await _run_cli(spawn_error=FileNotFoundError("no such file"))
    assert error_category(result) == "unavailable"


async def test_cli_spawn_os_error_is_not_categorised(anthropic_key: None) -> None:
    result = await _run_cli(spawn_error=PermissionError("not executable"))
    assert error_category(result) is None


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"API Error: 429 rate limit exceeded", "rate_limit"),
        (b"Credit balance is too low", "out_of_credits"),
        (b"SyntaxError: unexpected token", None),
        (b"Invalid API key \xc2\xb7 Please run /login", None),
    ],
)
async def test_cli_exit_is_categorised_from_stderr(
    anthropic_key: None, stderr: bytes, expected: str | None
) -> None:
    result = await _run_cli(build_subprocess_mock(stderr=stderr, returncode=1))
    assert error_category(result) == expected
    assert result.errors[0].startswith("Claude CLI exited with code 1")


async def test_cli_reported_error_is_categorised_from_its_text(anthropic_key: None) -> None:
    payload = (
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"result":"Credit balance is too low","usage":{}}'
    )
    result = await _run_cli(build_subprocess_mock(stdout=payload, returncode=0))
    assert error_category(result) == "out_of_credits"
    assert result.errors == ["Claude CLI reported an error: Credit balance is too low"]


async def test_cli_config_error_has_no_category(anthropic_key: None) -> None:
    with patch(_WHICH, return_value="/usr/local/bin/claude"):
        result = await ClaudeCodeAdapter().execute(_cli_task(extra_args="not-a-list"))
    assert result.success is False
    assert error_category(result) is None


# --------------------------------------------------------------------------- http

_HTTP_CLIENT = "dap_runtimes.adapters.http.httpx.AsyncClient"


def _http_task() -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-934",
        prompt_xml="<p/>",
        working_directory="/tmp",
        timeout_ms=1_000,
        runtime_config={
            "url": "http://localhost:11434/api/generate",
            "method": "POST",
            "request_template": {"prompt": "{{ prompt_xml }}"},
            "response_extractor": {"output": "$.response"},
        },
    )


async def _run_http(*, response: httpx.Response | None = None, error: Exception | None = None) -> RuntimeResult:
    client = MagicMock()
    client.request = AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(_HTTP_CLIENT, return_value=client):
        result = await HttpAdapter().execute(_http_task())
    assert result.success is False
    return result


_REQ = httpx.Request("POST", "http://localhost:11434/api/generate")


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limit"), (500, "unavailable"), (502, "unavailable"), (503, "unavailable"),
     (400, None), (401, None), (404, None), (422, None)],
)
async def test_http_status_is_categorised(status: int, expected: str | None) -> None:
    result = await _run_http(response=httpx.Response(status, text="nope", request=_REQ))
    assert error_category(result) == expected
    assert result.errors == [f"HTTP {status}: nope"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout("slow", request=_REQ), "timeout"),
        (httpx.ConnectTimeout("slow", request=_REQ), "timeout"),
        (httpx.ConnectError("connection refused", request=_REQ), "unavailable"),
        (httpx.RemoteProtocolError("peer closed", request=_REQ), "unavailable"),
        (httpx.UnsupportedProtocol("ftp://", request=_REQ), None),
    ],
)
async def test_http_transport_errors_are_categorised(error: Exception, expected: str | None) -> None:
    result = await _run_http(error=error)
    assert error_category(result) == expected


async def test_http_non_json_body_is_not_a_backend_error() -> None:
    result = await _run_http(response=httpx.Response(200, text="plain", request=_REQ))
    assert error_category(result) is None


# --------------------------------------------------------------------------- github


def _github(handler: Any) -> GithubAdapter:
    return GithubAdapter(transport=httpx.MockTransport(handler))


def _github_task(op: dict[str, Any]) -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-934",
        prompt_xml="<prompt/>",
        working_directory=".",
        runtime_config={"repo": "acme/widgets", **op, "__pipeline_state": {}},
        project_env_vars={"GH_TOKEN": "ghp_test_token_934"},
    )


_READ_ISSUE = {"op": "read_issue", "issue": 7}


@pytest.fixture(autouse=True)
def _no_engine_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "CORTEX_GH_TOKEN_READ"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (500, {}, "unavailable"),
        (502, {}, "unavailable"),
        (503, {}, "unavailable"),
        (429, {}, "rate_limit"),
        (403, {"x-ratelimit-remaining": "0"}, "rate_limit"),
        (403, {}, None),
        (401, {}, None),
        (404, {}, None),
    ],
)
async def test_github_status_is_categorised(
    status: int, headers: dict[str, str], expected: str | None
) -> None:
    result = await _github(
        lambda _r: httpx.Response(status, json={"message": "nope"}, headers=headers)
    ).execute(_github_task(_READ_ISSUE))
    assert result.success is False
    assert error_category(result) == expected


async def test_github_secondary_rate_limit_message_is_a_rate_limit() -> None:
    body = {"message": "You have exceeded a secondary rate limit."}
    result = await _github(lambda _r: httpx.Response(403, json=body)).execute(
        _github_task(_READ_ISSUE)
    )
    assert error_category(result) == "rate_limit"


@pytest.mark.parametrize(
    ("error_cls", "expected"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "unavailable"),
    ],
)
async def test_github_transport_errors_are_categorised(
    error_cls: type[httpx.RequestError], expected: str
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise error_cls("down", request=request)

    result = await _github(boom).execute(_github_task(_READ_ISSUE))
    assert result.success is False
    assert error_category(result) == expected


async def test_github_merge_keeps_the_category_through_its_rewrap() -> None:
    result = await _github(lambda _r: httpx.Response(502, json={"message": "bad gateway"})).execute(
        _github_task({"op": "merge_pr", "pr": 12, "expected_head_sha": "a" * 40})
    )
    assert result.success is False
    assert error_category(result) == "unavailable"
    assert "expected head" in result.errors[0]


async def test_github_config_error_has_no_category() -> None:
    result = await _github(lambda _r: httpx.Response(200, json={})).execute(
        _github_task({"op": "no_such_op"})
    )
    assert result.success is False
    assert error_category(result) is None


# --------------------------------------------------------------------------- explainer


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (
            anthropic.RateLimitError("Rate limit exceeded", response=_anthropic_response(429), body=None),
            "rate_limit",
        ),
        (
            anthropic.BadRequestError(
                "Your credit balance is too low", response=_anthropic_response(400), body=None
            ),
            "out_of_credits",
        ),
    ],
)
async def test_explainer_reads_the_same_category_from_the_message(
    provider_keys: None, error: Exception, category: str
) -> None:
    result = await _run_anthropic(error)
    assert error_category(result) == category
    explanation = explain_node_error(result.errors[0], runtime_id="api-call")
    assert explanation.recognized
    expected_phrase = "rate limit" if category == "rate_limit" else "credit balance"
    assert expected_phrase in explanation.cause.lower()
