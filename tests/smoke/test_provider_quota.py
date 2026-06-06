"""Provider failure classification — out-of-credits vs rate-limit (#692).

Providers must distinguish a non-recoverable quota/credit exhaustion from a
transient rate limit so the operator (and, later, notifications/UI) can react
correctly instead of reading a raw 429.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers._base import (
    ProviderError,
    classify_provider_failure,
)
from dap_types import RuntimeTask

_OPENAI_CLIENT_PATH = "dap_runtimes.adapters._providers._openai.AsyncOpenAI"


# ---------------------------------------------------------------------------
# Unit — classify_provider_failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status", "expected"),
    [
        # OpenAI delivers insufficient_quota as a 429 — must NOT read as rate limit.
        (
            "Error code: 429 - You exceeded your current quota (insufficient_quota)",
            429,
            "out_of_credits",
        ),
        ("insufficient_quota", None, "out_of_credits"),
        # Anthropic credit exhaustion arrives as a 400.
        (
            "Your credit balance is too low to access the Anthropic API",
            400,
            "out_of_credits",
        ),
        ("billing_hard_limit_reached", 429, "out_of_credits"),
        ("please check your plan and billing details", None, "out_of_credits"),
        # Transient throttling.
        ("Rate limit reached for gpt-5-mini", 429, "rate_limit"),
        ("Too many requests", None, "rate_limit"),
        ("429 RESOURCE_EXHAUSTED", 429, "rate_limit"),
        # Everything else is uncategorised.
        ("Model not found", 404, None),
        ("Authentication failed: invalid api key", 401, None),
        ("", None, None),
    ],
)
def test_classify_provider_failure(text: str, status: int | None, expected: str | None) -> None:
    assert classify_provider_failure(text, status_code=status) == expected


def test_provider_error_carries_category() -> None:
    err = ProviderError("Out of credits / quota: boom", category="out_of_credits")
    assert err.category == "out_of_credits"
    assert "Out of credits" in str(err)
    # Default stays None for the generic path.
    assert ProviderError("Bad request: nope").category is None


# ---------------------------------------------------------------------------
# Integration — the OpenAI adapter surfaces the classified label
# ---------------------------------------------------------------------------


@pytest.fixture
def with_openai_key() -> Iterator[None]:
    original = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original


def _task() -> RuntimeTask:
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>x</role></agent_prompt>",
        working_directory="/tmp",
        runtime_config={
            "provider": "openai",
            "model_id": "gpt-5-mini",
            "max_tokens": 1024,
        },
    )


def _openai_error(
    cls: type[openai.APIStatusError], message: str, status: int
) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


async def _run_with_openai_error(error: Exception) -> list[str]:
    adapter = ApiCallAdapter()
    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=error)
        client.close = AsyncMock()
        mock_cls.return_value = client
        result = await adapter.execute(_task())
    assert result.success is False
    return list(result.errors)


async def test_openai_insufficient_quota_reads_as_out_of_credits(
    with_openai_key: None,
) -> None:
    errors = await _run_with_openai_error(
        _openai_error(
            openai.RateLimitError,
            "Error code: 429 - You exceeded your current quota, please check "
            "your plan and billing details. (insufficient_quota)",
            429,
        )
    )
    assert any("Out of credits" in e for e in errors), errors
    assert not any("Rate limited" in e for e in errors), errors


async def test_openai_plain_rate_limit_reads_as_rate_limited(
    with_openai_key: None,
) -> None:
    errors = await _run_with_openai_error(
        _openai_error(
            openai.RateLimitError,
            "Error code: 429 - Rate limit reached for gpt-5-mini in "
            "organization org-x. Please try again later.",
            429,
        )
    )
    assert any("Rate limited" in e for e in errors), errors
    assert not any("Out of credits" in e for e in errors), errors
