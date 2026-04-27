"""Tests for the Gemini provider — Google Gen AI SDK is mocked."""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers._gemini import _calculate_cost
from dap_types import RuntimeTask

_GEMINI_CLIENT_PATH = "dap_runtimes.adapters._providers._gemini.genai.Client"


@pytest.fixture
def with_gemini_key() -> Iterator[None]:
    original = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = "gem-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original


def _task(**runtime_config_overrides: Any) -> RuntimeTask:
    runtime_config: dict[str, Any] = {
        "provider": "gemini",
        "model_id": "gemini-3.0-flash",
        "max_tokens": 1024,
    }
    runtime_config.update(runtime_config_overrides)
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>x</role></agent_prompt>",
        working_directory="/tmp",
        runtime_config=runtime_config,
    )


def _mock_response(
    text: str = "ok",
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cached_tokens: int = 0,
    finish_reason: str = "STOP",
) -> SimpleNamespace:
    """Minimal google.genai response-like object."""
    usage = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=completion_tokens,
        cached_content_token_count=cached_tokens,
    )
    candidate = SimpleNamespace(finish_reason=finish_reason)
    return SimpleNamespace(
        text=text,
        usage_metadata=usage,
        candidates=[candidate],
    )


# ---------------------------------------------------------------------------
# Successful execute
# ---------------------------------------------------------------------------


async def test_gemini_success(with_gemini_key: None) -> None:
    adapter = ApiCallAdapter()
    fake = _mock_response(text="hi from gemini", prompt_tokens=200, completion_tokens=120)

    with patch(_GEMINI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=fake)
        mock_cls.return_value = client

        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "hi from gemini"
    assert result.tokens_used == 320
    assert result.cost_usd is not None
    # gemini-3.0-flash: $0.30 input, $2.50 output
    # (200 * 0.30 + 120 * 2.50) / 1M = (60 + 300) / 1M = 0.00036
    assert abs(result.cost_usd - 0.00036) < 1e-9
    assert result.structured is not None
    assert result.structured["provider"] == "gemini"


async def test_gemini_passes_xml_as_system_instruction(with_gemini_key: None) -> None:
    adapter = ApiCallAdapter()
    fake = _mock_response()

    with patch(_GEMINI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=fake)
        mock_cls.return_value = client

        await adapter.execute(_task())

        call_kwargs = client.aio.models.generate_content.call_args.kwargs

    assert call_kwargs["model"] == "gemini-3.0-flash"
    assert "<agent_prompt>" in call_kwargs["config"]["system_instruction"]
    assert call_kwargs["config"]["max_output_tokens"] == 1024
    # User-side `contents` is the trigger message, not the prompt itself
    assert "<agent_prompt>" not in call_kwargs["contents"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_gemini_missing_api_key() -> None:
    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        adapter = ApiCallAdapter()
        result = await adapter.execute(_task())
        assert result.success is False
        assert any("GEMINI_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


async def test_gemini_missing_model_id_returns_error(with_gemini_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(model_id=""))
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def test_cost_calc_pro() -> None:
    # gemini-3.0-pro: $1.25 input, $10.00 output
    cost = _calculate_cost("gemini-3.0-pro", 1_000_000, 1_000_000)
    assert cost == pytest.approx(11.25)


def test_cost_calc_flash() -> None:
    cost = _calculate_cost("gemini-3.0-flash", 1_000_000, 1_000_000)
    assert cost == pytest.approx(2.80)


def test_cost_calc_unknown_model() -> None:
    assert _calculate_cost("gemini-future-99", 100, 100) is None
