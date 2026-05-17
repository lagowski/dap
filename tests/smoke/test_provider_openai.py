"""Tests for the OpenAI provider — both `openai` and `openai-compat` modes.

SDK is mocked; gated real-API tests can be added behind DAP_E2E_LIVE_LLMS=1
once the engine has a stable mock pattern for OpenAI errors.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers._openai import _calculate_cost
from dap_types import RuntimeTask

_OPENAI_CLIENT_PATH = "dap_runtimes.adapters._providers._openai.AsyncOpenAI"


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


@pytest.fixture
def with_glm_key() -> Iterator[None]:
    original = os.environ.get("GLM_API_KEY")
    os.environ["GLM_API_KEY"] = "glm-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("GLM_API_KEY", None)
        else:
            os.environ["GLM_API_KEY"] = original


@pytest.fixture
def with_openrouter_key() -> Iterator[None]:
    original = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "or-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = original


def _task(provider: str = "openai", **runtime_config_overrides: Any) -> RuntimeTask:
    runtime_config: dict[str, Any] = {
        "provider": provider,
        "model_id": "gpt-5-mini",
        "max_tokens": 1024,
    }
    runtime_config.update(runtime_config_overrides)
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>x</role></agent_prompt>",
        working_directory="/tmp",
        runtime_config=runtime_config,
    )


def _mock_chat_completion(
    text: str = "ok",
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cached_tokens: int = 0,
    model: str = "gpt-5-mini",
) -> SimpleNamespace:
    """Minimal openai.types.chat.ChatCompletion-like object."""
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(
        message=message,
        finish_reason="stop",
    )
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )
    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model=model,
    )


# ---------------------------------------------------------------------------
# Native OpenAI
# ---------------------------------------------------------------------------


async def test_openai_success(with_openai_key: None) -> None:
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="hello", prompt_tokens=200, completion_tokens=80)

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "hello"
    assert result.tokens_used == 280
    assert result.cost_usd is not None
    # gpt-5-mini: $0.25 / $1.00 per 1M
    # (200 * 0.25 + 80 * 1.00) / 1M = (50 + 80) / 1M = 0.00013
    assert abs(result.cost_usd - 0.00013) < 1e-9
    assert result.structured is not None
    assert result.structured["provider"] == "openai"
    assert result.structured["stop_reason"] == "stop"


async def test_openai_missing_api_key_returns_error() -> None:
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        adapter = ApiCallAdapter()
        result = await adapter.execute(_task())
        assert result.success is False
        assert any("OPENAI_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


async def test_openai_missing_model_id_returns_error(with_openai_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(_task(model_id=""))
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


# ---------------------------------------------------------------------------
# OpenAI-compat (GLM, Together, OpenRouter, etc.)
# ---------------------------------------------------------------------------


async def test_openai_compat_requires_base_url(with_glm_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(
        _task(
            provider="openai-compat",
            model_id="glm-5-flash",
            api_key_env="GLM_API_KEY",
        )
    )
    assert result.success is False
    assert any("base_url" in e for e in result.errors)


async def test_openai_compat_requires_api_key_env(with_glm_key: None) -> None:
    adapter = ApiCallAdapter()
    result = await adapter.execute(
        _task(
            provider="openai-compat",
            model_id="glm-5-flash",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )
    )
    assert result.success is False
    assert any("api_key_env" in e for e in result.errors)


async def test_openai_compat_uses_custom_env_var(with_glm_key: None) -> None:
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="from glm", model="glm-5-flash")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(
            _task(
                provider="openai-compat",
                model_id="glm-5-flash",
                base_url="https://api.z.ai/api/coding/paas/v4",
                api_key_env="GLM_API_KEY",
            )
        )

        ctor_kwargs = mock_cls.call_args.kwargs

    assert result.success is True
    assert ctor_kwargs["api_key"] == "glm-test-fake"
    assert ctor_kwargs["base_url"] == "https://api.z.ai/api/coding/paas/v4"
    # Cost is None for compat — we don't know third-party pricing
    assert result.cost_usd is None
    assert result.structured is not None
    assert result.structured["provider"] == "openai-compat"


async def test_openai_compat_missing_named_env_var() -> None:
    """If api_key_env names a var that isn't set, validate fails clearly."""
    saved = os.environ.pop("GLM_API_KEY", None)
    try:
        adapter = ApiCallAdapter()
        result = await adapter.execute(
            _task(
                provider="openai-compat",
                model_id="glm-5-flash",
                base_url="https://api.z.ai/api/coding/paas/v4",
                api_key_env="GLM_API_KEY",
            )
        )
        assert result.success is False
        assert any("GLM_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["GLM_API_KEY"] = saved


# ---------------------------------------------------------------------------
# GLM (#115) — first-class OpenAI-compatible provider
# ---------------------------------------------------------------------------


async def test_glm_provider_uses_hardcoded_base_url_and_env_var(
    with_glm_key: None,
) -> None:
    """``provider: \"glm\"`` doesn't require base_url / api_key_env on the
    agent — the provider hardcodes both. SDK call should land at z.ai
    with ``GLM_API_KEY`` for auth."""
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="from glm", model="glm-5-flash")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(
            _task(provider="glm", model_id="glm-5-flash"),
        )
        ctor_kwargs = mock_cls.call_args.kwargs

    assert result.success is True
    assert ctor_kwargs["api_key"] == "glm-test-fake"
    assert ctor_kwargs["base_url"] == "https://api.z.ai/api/coding/paas/v4"
    assert result.structured is not None
    assert result.structured["provider"] == "glm"


async def test_glm_provider_missing_env_var_returns_error() -> None:
    """No ``GLM_API_KEY`` → validate_config refuses before SDK call."""
    saved = os.environ.pop("GLM_API_KEY", None)
    try:
        adapter = ApiCallAdapter()
        result = await adapter.execute(
            _task(provider="glm", model_id="glm-5-flash"),
        )
        assert result.success is False
        assert any("GLM_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["GLM_API_KEY"] = saved


async def test_glm_provider_does_not_require_base_url_or_api_key_env(
    with_glm_key: None,
) -> None:
    """The two openai-compat-specific fields are optional for ``glm``;
    leaving them out should not produce a 'required' error."""
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="ok", model="glm-5-flash")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(
            _task(provider="glm", model_id="glm-5-flash"),
        )

    assert result.success is True


# ---------------------------------------------------------------------------
# OpenRouter (#449) — first-class OpenAI-compatible multi-model gateway
# ---------------------------------------------------------------------------


async def test_openrouter_provider_uses_hardcoded_base_url_and_env_var(
    with_openrouter_key: None,
) -> None:
    """``provider: "openrouter"`` doesn't require base_url / api_key_env on
    the agent — the provider hardcodes both. SDK call should land at
    OpenRouter with ``OPENROUTER_API_KEY`` for auth."""
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="from openrouter", model="anthropic/claude-3.5-sonnet")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(
            _task(provider="openrouter", model_id="anthropic/claude-3.5-sonnet"),
        )
        ctor_kwargs = mock_cls.call_args.kwargs

    assert result.success is True
    assert ctor_kwargs["api_key"] == "or-test-fake"
    assert ctor_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert result.structured is not None
    assert result.structured["provider"] == "openrouter"


async def test_openrouter_provider_injects_referer_and_title_headers(
    with_openrouter_key: None,
) -> None:
    """OpenRouter convention: requests carry ``HTTP-Referer`` + ``X-Title``
    so they show up labelled in the OpenRouter dashboard's traffic log.
    Headers are passed via the SDK's ``default_headers`` constructor arg
    — no auth or billing impact, just attribution."""
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="ok", model="deepseek/deepseek-v3-pro")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        await adapter.execute(
            _task(provider="openrouter", model_id="deepseek/deepseek-v3-pro"),
        )
        ctor_kwargs = mock_cls.call_args.kwargs

    headers = ctor_kwargs.get("default_headers")
    assert headers is not None, (
        "openrouter must pass default_headers to AsyncOpenAI for attribution"
    )
    assert "HTTP-Referer" in headers
    assert "X-Title" in headers
    assert headers["X-Title"] == "DAP"
    # The referer should be a real URL pointing at DAP, not a placeholder.
    assert headers["HTTP-Referer"].startswith("https://")


async def test_openrouter_provider_missing_env_var_returns_error() -> None:
    """No ``OPENROUTER_API_KEY`` → validate_config refuses before SDK call.
    The error message must name the canonical env var so the operator
    knows what to set without spelunking through provider source."""
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        adapter = ApiCallAdapter()
        result = await adapter.execute(
            _task(provider="openrouter", model_id="anthropic/claude-3.5-sonnet"),
        )
        assert result.success is False
        assert any("OPENROUTER_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved


async def test_openrouter_provider_does_not_require_base_url_or_api_key_env(
    with_openrouter_key: None,
) -> None:
    """The two openai-compat-specific fields are optional for
    ``openrouter``; leaving them out should not produce a 'required'
    error. This is the core ergonomic win over the legacy
    ``openai-compat`` recipe — one knob (``provider``) instead of three."""
    adapter = ApiCallAdapter()
    fake = _mock_chat_completion(text="ok", model="anthropic/claude-3.5-sonnet")

    with patch(_OPENAI_CLIENT_PATH) as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=fake)
        client.close = AsyncMock()
        mock_cls.return_value = client

        result = await adapter.execute(
            _task(provider="openrouter", model_id="anthropic/claude-3.5-sonnet"),
        )

    assert result.success is True


def test_openrouter_registered_with_canonical_env_var() -> None:
    """``openrouter`` must be listed by ``list_provider_ids`` and its
    metadata must point at the OpenAI module + ``OPENROUTER_API_KEY``.
    This is the wiring the dashboard's runtime picker + Settings page
    pick up automatically once the registry entry is in place."""
    from dap_runtimes.adapters._providers import (
        get_provider_info,
        list_provider_ids,
    )

    assert "openrouter" in list_provider_ids()
    info = get_provider_info("openrouter")
    assert info is not None
    assert info.default_env_var == "OPENROUTER_API_KEY"
    # Same module as openai / openai-compat / glm — proves we're reusing
    # the OpenAI SDK dispatch path, not introducing a parallel provider.
    assert info.module_path == "dap_runtimes.adapters._providers._openai"


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def test_cost_calc_gpt5() -> None:
    # gpt-5: $2.50 input, $10.00 output
    cost = _calculate_cost("gpt-5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(12.5)


def test_cost_calc_gpt5_mini() -> None:
    cost = _calculate_cost("gpt-5-mini", 1_000_000, 1_000_000)
    assert cost == pytest.approx(1.25)


def test_cost_calc_unknown_model() -> None:
    assert _calculate_cost("future-99", 100, 100) is None
