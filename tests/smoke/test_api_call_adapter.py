"""Tests for the api-call (multi-provider) runtime adapter.

Mocks the underlying SDKs — see DAP_E2E_LIVE_LLMS=1 to opt into real calls.

Provider-specific tests live in their own files (test_provider_anthropic,
_openai, _gemini); this file covers the dispatcher + the Anthropic happy
path so the existing coverage carries over.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers._anthropic import _calculate_cost
from dap_types import RuntimeTask

_ANTHROPIC_CLIENT_PATH = "dap_runtimes.adapters._providers._anthropic.AsyncAnthropic"


@pytest.fixture
def with_api_key() -> Iterator[None]:
    """Inject a fake ANTHROPIC_API_KEY for the duration of the test."""
    original = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original


def _task(**runtime_config_overrides: Any) -> RuntimeTask:
    runtime_config: dict[str, Any] = {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5",
        "max_tokens": 1024,
    }
    runtime_config.update(runtime_config_overrides)
    return RuntimeTask(
        execution_id="exec-001",
        prompt_xml="<agent_prompt><role>test</role><task>echo</task></agent_prompt>",
        working_directory="/tmp",
        runtime_config=runtime_config,
    )


def _mock_message(text: str = "ok", **usage_overrides: int) -> SimpleNamespace:
    """Build a minimal anthropic.types.Message-like object."""
    usage_kwargs: dict[str, int] = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    usage_kwargs.update(usage_overrides)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model="claude-haiku-4-5",
        stop_reason="end_turn",
        usage=SimpleNamespace(**usage_kwargs),
    )


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_without_any_provider_configured(
    no_provider_env: None,
) -> None:
    adapter = ApiCallAdapter()
    health = await adapter.healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("ANTHROPIC_API_KEY" in m for m in health.missing)


async def test_healthcheck_with_anthropic_configured(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    health = await adapter.healthcheck()
    assert health.available is True
    # `version` is the comma-separated list of providers whose env var
    # is set — fast read straight from the registry, no SDK import.
    assert health.version is not None
    assert "anthropic" in health.version


# ---------------------------------------------------------------------------
# Dispatcher: provider selection
# ---------------------------------------------------------------------------


def test_provider_registry_holds_only_metadata() -> None:
    """Registry must store module *paths* (strings), not imported modules.

    Storing imported modules would make ``import dap_runtimes`` pull every
    SDK eagerly (anthropic + openai + google-genai). Verifying the data
    shape is enough — the only way to dispatch lazily is via importlib,
    which only happens inside ``get_provider()``. Avoids touching
    ``sys.modules`` (which pollutes state for other tests).
    """
    from dap_runtimes.adapters._providers import (
        PROVIDER_REGISTRY,
        ProviderInfo,
    )

    assert len(PROVIDER_REGISTRY) >= 3
    for info in PROVIDER_REGISTRY.values():
        assert isinstance(info, ProviderInfo)
        # module_path is the stringly-named module, not the imported module
        assert isinstance(info.module_path, str)
        assert info.module_path.startswith("dap_runtimes.adapters._providers._")
        assert info.config_fields
        assert any(field.key == "model_id" and field.required for field in info.config_fields)


async def test_unknown_provider_returns_error(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(provider="not-a-provider"))
    assert result.success is False
    assert any("not-a-provider" in e.lower() for e in result.errors)


async def test_default_provider_is_anthropic(with_api_key: None) -> None:
    """Omitting `provider` falls back to anthropic for backward compat."""
    adapter = ApiCallAdapter()
    fake_message = _mock_message()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_message)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        # _task() defaults provider="anthropic"; remove to test the default
        task = _task()
        task.runtime_config.pop("provider")
        result = await adapter.execute(task)

    assert result.success is True


# ---------------------------------------------------------------------------
# Anthropic config validation
# ---------------------------------------------------------------------------


async def test_missing_api_key_returns_error(no_provider_env: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task())
    assert result.success is False
    assert any("ANTHROPIC_API_KEY" in e for e in result.errors)


async def test_missing_model_id_returns_error(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(model_id=""))
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


async def test_invalid_max_tokens_returns_error(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(max_tokens=0))
    assert result.success is False
    assert any("max_tokens" in e for e in result.errors)


async def test_invalid_effort_returns_error(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(effort="invalid"))
    assert result.success is False
    assert any("effort" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Successful execute (mocked Anthropic SDK)
# ---------------------------------------------------------------------------


async def test_execute_success_returns_text_and_tokens(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    fake_message = _mock_message(text="Hello world")

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_message)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "Hello world"
    # 100 input + 50 output, no cache
    assert result.tokens_used == 150
    assert result.cost_usd is not None
    # Haiku: $1 input, $5 output → (100 + 50*5) / 1M = 0.00035
    assert abs(result.cost_usd - 0.00035) < 1e-9
    assert result.structured is not None
    assert result.structured["stop_reason"] == "end_turn"
    assert result.structured["provider"] == "anthropic"


async def test_execute_passes_xml_as_system(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    fake_message = _mock_message()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_message)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        await adapter.execute(_task())

        call_kwargs = mock_client.messages.create.call_args.kwargs

    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["max_tokens"] == 1024
    assert "<agent_prompt>" in call_kwargs["system"]
    # Default user message — XML is in system, not user
    assert call_kwargs["messages"][0]["role"] == "user"
    assert "<agent_prompt>" not in call_kwargs["messages"][0]["content"]
    # No optional features on by default
    assert "cache_control" not in call_kwargs
    assert "thinking" not in call_kwargs
    assert "output_config" not in call_kwargs


async def test_execute_with_optional_features(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    fake_message = _mock_message()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_message)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        await adapter.execute(
            _task(
                model_id="claude-opus-4-7",
                prompt_cache=True,
                enable_thinking=True,
                effort="xhigh",
                system_prompt="Extra instructions.",
            )
        )

        call_kwargs = mock_client.messages.create.call_args.kwargs

    assert call_kwargs["cache_control"] == {"type": "ephemeral"}
    assert call_kwargs["thinking"] == {"type": "adaptive"}
    assert call_kwargs["output_config"] == {"effort": "xhigh"}
    # system_prompt is prepended before XML
    assert call_kwargs["system"].startswith("Extra instructions.")


async def test_execute_with_prompt_caching_usage(with_api_key: None) -> None:
    adapter = ApiCallAdapter()
    # Simulate cache write (200) + cache read (1000) + uncached input (50) + output (100)
    fake_message = _mock_message(
        input_tokens=50,
        cache_creation_input_tokens=200,
        cache_read_input_tokens=1000,
        output_tokens=100,
    )

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=fake_message)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        result = await adapter.execute(_task(prompt_cache=True))

    assert result.success is True
    assert result.tokens_used == 50 + 200 + 1000 + 100
    # Haiku: $1 in, $5 out
    # cost = (50*1 + 200*1*1.25 + 1000*1*0.1 + 100*5) / 1M
    #      = (50 + 250 + 100 + 500) / 1M = 900 / 1M = 0.0009
    assert result.cost_usd is not None
    assert abs(result.cost_usd - 0.0009) < 1e-9


# ---------------------------------------------------------------------------
# Anthropic error path mapping
# ---------------------------------------------------------------------------


async def test_authentication_error(with_api_key: None) -> None:
    import anthropic

    adapter = ApiCallAdapter()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        err = anthropic.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )
        mock_client.messages.create = AsyncMock(side_effect=err)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        result = await adapter.execute(_task())

    assert result.success is False
    assert any("Authentication" in e for e in result.errors)


async def test_rate_limit_error(with_api_key: None) -> None:
    import anthropic

    adapter = ApiCallAdapter()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        err = anthropic.RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(),
            body=None,
        )
        mock_client.messages.create = AsyncMock(side_effect=err)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        result = await adapter.execute(_task())

    assert result.success is False
    assert any("Rate limited" in e for e in result.errors)


async def test_unexpected_exception_returns_failed_not_raised(with_api_key: None) -> None:
    """A non-ProviderError exception from provider.call must be caught (#211).

    Previously only ProviderError was caught; any other exception (SDK bug,
    lazy-import failure, network error outside the provider's typed wrappers)
    propagated to the worker and crashed it. The fix matches the family-wide
    _failed contract so unexpected exceptions surface as structured failures.
    """
    adapter = ApiCallAdapter()

    with patch(_ANTHROPIC_CLIENT_PATH) as mock_cls:
        mock_client = MagicMock()
        # Plain ValueError — not derived from anthropic.APIError, so the
        # provider would not wrap it into ProviderError. Pre-#211 this
        # propagated out of execute().
        mock_client.messages.create = AsyncMock(
            side_effect=ValueError("simulated SDK bug"),
        )
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        result = await adapter.execute(_task())

    assert result.success is False
    assert any("ValueError" in e and "simulated SDK bug" in e for e in result.errors)
    # Structured-failure shape: empty/None telemetry, non-negative duration
    # (can be 0 on fast failures due to integer truncation).
    assert result.tokens_used is None or result.tokens_used == 0
    assert result.cost_usd is None or result.cost_usd == 0.0
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Cost calculation unit tests (Anthropic pricing)
# ---------------------------------------------------------------------------


def test_cost_calc_haiku() -> None:
    # 1M input + 1M output @ $1 + $5 = $6
    cost = _calculate_cost("claude-haiku-4-5", 1_000_000, 0, 0, 1_000_000)
    assert cost == pytest.approx(6.0)


def test_cost_calc_opus() -> None:
    # 1M input + 1M output @ $5 + $25 = $30
    cost = _calculate_cost("claude-opus-4-7", 1_000_000, 0, 0, 1_000_000)
    assert cost == pytest.approx(30.0)


def test_cost_calc_sonnet_with_cache() -> None:
    # Sonnet 4.6 = $3 input, $15 output
    # 1M input + 1M cache write (1.25x) + 1M cache read (0.1x) + 1M output
    # = 3 + 3.75 + 0.3 + 15 = 22.05
    cost = _calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert cost == pytest.approx(22.05)


def test_cost_calc_unknown_model() -> None:
    cost = _calculate_cost("claude-future-99", 100, 0, 0, 100)
    assert cost is None
